"""OpenAI structured-output wrapper bound to EventGraphSettings (D6)."""

from __future__ import annotations

import asyncio
import time
from typing import TypeVar

import httpx
from openai import AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

from app.pipeline.event_graph.config import settings

T = TypeVar("T", bound=BaseModel)

_semaphore = asyncio.Semaphore(settings.EVENT_GRAPH_LLM_CONCURRENCY)
_token_usage: dict[str, int] = {}
_client: AsyncOpenAI | None = None
_circuit_lock = asyncio.Lock()
_unavailable_failures = 0
_circuit_open_until = 0.0
_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_COOLDOWN_SECONDS = 20.0


class LLMValidationError(Exception):
    """Raised when structured output fails Pydantic validation (no retry)."""


def _request_timeout() -> httpx.Timeout:
    """Connect fails fast; read waits for the full local-model generation."""
    read = float(settings.EVENT_GRAPH_LLM_TIMEOUT)
    return httpx.Timeout(connect=10.0, read=read, write=60.0, pool=10.0)


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL or None,
            timeout=_request_timeout(),
        )
    return _client


_MODEL_UNAVAILABLE_MARKERS = (
    "no models loaded",
    "connection entered error state",
    "peer_keepalive_timeout",
)


def _is_model_unavailable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status != 400:
        return False
    text = str(getattr(exc, "message", None) or exc).casefold()
    return any(marker in text for marker in _MODEL_UNAVAILABLE_MARKERS)


def _is_transient(exc: BaseException) -> bool:
    # A read timeout is NOT retried: if a call exceeds EVENT_GRAPH_LLM_TIMEOUT it
    # will do so again on an identical retry (same prompt, same slow generation),
    # so 5 attempts just burn ~5x the timeout. Cap output size / raise the
    # timeout instead. Genuine transients (rate limit, 5xx, model unloaded) retry.
    if isinstance(exc, RateLimitError):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 500:
        return True
    return _is_model_unavailable(exc)


_WAIT_MODEL_UNAVAILABLE = wait_exponential(multiplier=2, min=2, max=30)
_WAIT_FAST_TRANSIENT = wait_exponential(multiplier=2, min=1, max=8)


class _wait_by_error(wait_base):
    """Long backoff for model-unavailable; short backoff for other transients."""

    def __call__(self, retry_state) -> float:
        exc = None
        if retry_state.outcome is not None:
            exc = retry_state.outcome.exception()
        if exc is not None and _is_model_unavailable(exc):
            return float(_WAIT_MODEL_UNAVAILABLE(retry_state))
        return float(_WAIT_FAST_TRANSIENT(retry_state))


async def _await_circuit() -> None:
    async with _circuit_lock:
        remaining = _circuit_open_until - time.monotonic()
    if remaining > 0:
        await asyncio.sleep(remaining)


async def _record_unavailable_failure() -> None:
    global _unavailable_failures, _circuit_open_until
    async with _circuit_lock:
        _unavailable_failures += 1
        if _unavailable_failures >= _CIRCUIT_FAILURE_THRESHOLD:
            _circuit_open_until = time.monotonic() + _CIRCUIT_COOLDOWN_SECONDS


async def _record_success() -> None:
    global _unavailable_failures, _circuit_open_until
    async with _circuit_lock:
        _unavailable_failures = 0
        _circuit_open_until = 0.0


def get_token_usage(job_id: str) -> int:
    return _token_usage.get(job_id, 0)


def reset_token_usage(job_id: str | None = None) -> None:
    if job_id is None:
        _token_usage.clear()
    else:
        _token_usage.pop(job_id, None)


def reset_llm_client(concurrency: int | None = None) -> None:
    """Reset module-level client, semaphore, and circuit-breaker state (for tests)."""
    global _client, _semaphore, _circuit_lock, _unavailable_failures, _circuit_open_until
    _client = None
    _semaphore = asyncio.Semaphore(
        concurrency or settings.EVENT_GRAPH_LLM_CONCURRENCY
    )
    _token_usage.clear()
    _circuit_lock = asyncio.Lock()
    _unavailable_failures = 0
    _circuit_open_until = 0.0


@retry(
    reraise=True,
    stop=stop_after_attempt(5),
    wait=_wait_by_error(),
    retry=retry_if_exception(_is_transient),
)
async def _call_openai(
    system_prompt: str,
    user_prompt: str,
    response_model: type[T],
    temperature: float,
    job_id: str | None,
) -> T:
    await _await_circuit()
    client = _get_client()
    try:
        async with _semaphore:
            completion = await client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_model,
                temperature=temperature,
                max_tokens=settings.EVENT_GRAPH_LLM_MAX_TOKENS,
            )
    except BaseException as exc:
        if _is_model_unavailable(exc):
            await _record_unavailable_failure()
        raise
    await _record_success()
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise LLMValidationError("OpenAI returned empty structured output")
    if completion.usage is not None and job_id is not None:
        _token_usage[job_id] = _token_usage.get(job_id, 0) + completion.usage.total_tokens
    return parsed


async def call_structured(
    system_prompt: str,
    user_prompt: str,
    response_model: type[T],
    temperature: float = 0,
    job_id: str | None = None,
) -> T:
    """Call OpenAI with structured output, retry policy, and concurrency limit."""
    try:
        return await _call_openai(
            system_prompt, user_prompt, response_model, temperature, job_id
        )
    except ValidationError as exc:
        raise LLMValidationError(str(exc)) from exc
