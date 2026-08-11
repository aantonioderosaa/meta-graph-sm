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
async def test_timeout_retries_three_times_then_raises():
    mock_parse = AsyncMock(side_effect=APITimeoutError("timeout"))
    with patch.object(llm_client, "_get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=mock_parse))
            )
        )
        with pytest.raises(APITimeoutError):
            await call_structured("sys", "user", DummyModel)

    assert mock_parse.await_count == 3


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


@pytest.mark.asyncio
async def test_semaphore_limits_concurrent_calls():
    llm_client.reset_llm_client(concurrency=5)
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

    assert max_in_flight <= 5
    assert mock_parse.await_count == 6


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
