"""LLM client resilience tests (tech-spec §18, E2.4)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from openai import APITimeoutError
from pydantic import BaseModel, ValidationError

from app.core import llm_client
from app.core.llm_client import LLMValidationError, call_structured


class DummyModel(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def reset_llm_state():
    llm_client.reset_llm_client()
    yield
    llm_client.reset_llm_client()


def _completion(parsed, total_tokens: int = 10):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


@pytest.mark.asyncio
async def test_timeout_retries_five_times_then_raises(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    mock_parse = AsyncMock(side_effect=APITimeoutError("timeout"))
    with patch.object(llm_client, "_get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=mock_parse))
            )
        )
        with pytest.raises(APITimeoutError):
            await call_structured("sys", "user", DummyModel)

    assert mock_parse.await_count == 5


class _BadRequestLike(Exception):
    """Stand-in for openai.BadRequestError — real one needs a full httpx response."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.status_code = 400
        self.message = message


@pytest.mark.asyncio
async def test_model_unavailable_400_is_retried_then_recovers(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    mock_parse = AsyncMock(
        side_effect=[
            _BadRequestLike("No models loaded. Please load a model..."),
            _completion(DummyModel(value="ok")),
        ]
    )
    with patch.object(llm_client, "_get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=mock_parse))
            )
        )
        result = await call_structured("sys", "user", DummyModel)

    assert result == DummyModel(value="ok")
    assert mock_parse.await_count == 2


@pytest.mark.asyncio
async def test_timeout_uses_short_backoff(monkeypatch):
    sleeps: list[float] = []

    async def capture_sleep(delay, result=None):
        sleeps.append(delay)
        if result is not None:
            return result

    monkeypatch.setattr(asyncio, "sleep", capture_sleep)
    mock_parse = AsyncMock(side_effect=APITimeoutError("timeout"))
    with patch.object(llm_client, "_get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=mock_parse))
            )
        )
        with pytest.raises(APITimeoutError):
            await call_structured("sys", "user", DummyModel)

    assert mock_parse.await_count == 5
    assert sleeps == [2, 4, 8, 8]


@pytest.mark.asyncio
async def test_model_unavailable_uses_long_backoff(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(llm_client, "_CIRCUIT_FAILURE_THRESHOLD", 99)

    async def capture_sleep(delay, result=None):
        sleeps.append(delay)
        if result is not None:
            return result

    monkeypatch.setattr(asyncio, "sleep", capture_sleep)
    mock_parse = AsyncMock(side_effect=_BadRequestLike("No models loaded. Please load a model..."))
    with patch.object(llm_client, "_get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=mock_parse))
            )
        )
        with pytest.raises(_BadRequestLike):
            await call_structured("sys", "user", DummyModel)

    assert mock_parse.await_count == 5
    assert sleeps == [2, 4, 8, 16]


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_three_unavailable_and_blocks_until_cooldown(
    monkeypatch,
):
    llm_client.reset_llm_client(concurrency=4)
    now = [1000.0]
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: now[0])

    await asyncio.gather(
        llm_client._record_unavailable_failure(),
        llm_client._record_unavailable_failure(),
        llm_client._record_unavailable_failure(),
    )
    assert llm_client._unavailable_failures == 3
    assert llm_client._circuit_open_until == pytest.approx(1020.0)

    parse_count = 0
    sleeps: list[float] = []

    async def fake_parse(*_args, **_kwargs):
        nonlocal parse_count
        parse_count += 1
        return _completion(DummyModel(value="ok"))

    async def fake_sleep(delay, result=None):
        sleeps.append(delay)
        now[0] += delay
        if result is not None:
            return result

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    mock_parse = AsyncMock(side_effect=fake_parse)
    with patch.object(llm_client, "_get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=mock_parse))
            )
        )
        result = await call_structured("sys", "user", DummyModel)

    assert sleeps == [llm_client._CIRCUIT_COOLDOWN_SECONDS]
    assert parse_count == 1
    assert result == DummyModel(value="ok")
    assert llm_client._unavailable_failures == 0
    assert llm_client._circuit_open_until == 0.0


@pytest.mark.asyncio
async def test_unavailable_failures_increment_circuit_counter(monkeypatch):
    monkeypatch.setattr(llm_client, "_CIRCUIT_FAILURE_THRESHOLD", 99)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    mock_parse = AsyncMock(side_effect=_BadRequestLike("No models loaded. Please load a model..."))
    with patch.object(llm_client, "_get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=mock_parse))
            )
        )
        with pytest.raises(_BadRequestLike):
            await call_structured("sys", "user", DummyModel)

    assert mock_parse.await_count == 5
    assert llm_client._unavailable_failures == 5


@pytest.mark.asyncio
async def test_unrelated_400_is_not_retried():
    mock_parse = AsyncMock(side_effect=_BadRequestLike("invalid_request: bad schema"))
    with patch.object(llm_client, "_get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=mock_parse))
            )
        )
        with pytest.raises(_BadRequestLike):
            await call_structured("sys", "user", DummyModel)

    assert mock_parse.await_count == 1


@pytest.mark.asyncio
async def test_validation_error_no_retry():
    mock_parse = AsyncMock(
        return_value=_completion(None),
    )
    with patch.object(llm_client, "_get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=mock_parse))
            )
        )
        with pytest.raises(LLMValidationError):
            await call_structured("sys", "user", DummyModel)

    assert mock_parse.await_count == 1


@pytest.mark.asyncio
async def test_pydantic_validation_error_no_retry():
    validation_error = ValidationError.from_exception_data(
        "DummyModel",
        [{"type": "missing", "loc": ("value",), "msg": "Field required", "input": {}}],
    )

    mock_call = AsyncMock(side_effect=validation_error)
    with patch.object(llm_client, "_call_openai", mock_call):
        with pytest.raises(LLMValidationError):
            await call_structured("sys", "user", DummyModel)

    assert mock_call.await_count == 1


def test_request_timeout_uses_configured_read_seconds(monkeypatch):
    monkeypatch.setattr(llm_client.settings, "LLM_CALL_TIMEOUT_SECONDS", 180)
    timeout = llm_client._request_timeout()
    assert timeout.read == 180
    assert timeout.connect == 10.0


@pytest.mark.asyncio
async def test_semaphore_limits_concurrent_calls():
    llm_client.reset_llm_client(concurrency=4)
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def slow_parse(*_args, **_kwargs):
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return _completion(DummyModel(value="ok"))

    mock_parse = AsyncMock(side_effect=slow_parse)
    with patch.object(llm_client, "_get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=mock_parse))
            )
        )
        await asyncio.gather(
            *[call_structured("sys", "user", DummyModel) for _ in range(6)]
        )

    assert max_in_flight <= 4
    assert mock_parse.await_count == 6


@pytest.mark.asyncio
async def test_retry_backoff_releases_semaphore_between_attempts(monkeypatch):
    """A retry wait must not hold the concurrency slot (Macrotask 2)."""
    llm_client.reset_llm_client(concurrency=1)

    first_attempt_failed = asyncio.Event()
    second_parsed_during_backoff = asyncio.Event()
    parse_by_task: dict[str, int] = {}

    async def fake_parse(*_args, **_kwargs):
        name = asyncio.current_task().get_name()
        parse_by_task[name] = parse_by_task.get(name, 0) + 1
        if name == "call-a" and parse_by_task[name] == 1:
            first_attempt_failed.set()
            raise APITimeoutError("timeout")
        if name == "call-b":
            second_parsed_during_backoff.set()
        return _completion(DummyModel(value="ok"))

    async def gated_sleep(delay, result=None):
        # After call-a's first failure tenacity sleeps here. The second
        # call_structured must acquire the slot during that wait — if the
        # semaphore still wraps the whole retry, this times out.
        if first_attempt_failed.is_set() and not second_parsed_during_backoff.is_set():
            try:
                await asyncio.wait_for(second_parsed_during_backoff.wait(), timeout=1.0)
            except TimeoutError:
                pytest.fail(
                    "second call_structured did not acquire the semaphore during retry backoff"
                )
        if result is not None:
            return result

    monkeypatch.setattr(asyncio, "sleep", gated_sleep)

    mock_parse = AsyncMock(side_effect=fake_parse)
    with patch.object(llm_client, "_get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=mock_parse))
            )
        )

        async def run_a():
            return await call_structured("sys", "user", DummyModel)

        async def run_b():
            await first_attempt_failed.wait()
            return await call_structured("sys", "user", DummyModel)

        result_a, result_b = await asyncio.gather(
            asyncio.create_task(run_a(), name="call-a"),
            asyncio.create_task(run_b(), name="call-b"),
        )

    assert result_a == DummyModel(value="ok")
    assert result_b == DummyModel(value="ok")
    assert second_parsed_during_backoff.is_set()
    assert parse_by_task["call-a"] == 2
    assert parse_by_task["call-b"] == 1


@pytest.mark.asyncio
async def test_token_usage_accumulated_per_job():
    mock_parse = AsyncMock(return_value=_completion(DummyModel(value="ok"), total_tokens=42))
    with patch.object(llm_client, "_get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=mock_parse))
            )
        )
        await call_structured("sys", "user", DummyModel, job_id="job-1")
        await call_structured("sys", "user", DummyModel, job_id="job-1")

    assert llm_client.get_token_usage("job-1") == 84
