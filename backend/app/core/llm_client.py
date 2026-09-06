"""OpenAI structured-output wrapper with retry and concurrency (tech-spec §18, E2.4)."""

from __future__ import annotations

import asyncio
import time
from typing import TypeVar

import httpx
from openai import APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

from app.core.config import settings

T = TypeVar("T", bound=BaseModel)

_semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)
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
    read = float(settings.LLM_CALL_TIMEOUT_SECONDS)
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


# A local inference server (LM Studio et al.) can drop its loaded model mid-run
# under concurrent load — observed in practice: several chunks' full pairwise
# fan-out hitting the server at once exhausted it, and it started answering
# "No models loaded" / "connection entered error state" instead of serving
# requests. Both come back as a 400 BadRequestError, which used to fall
# through _is_transient untouched (only 5xx/timeout/rate-limit retried) and
# permanently drop whatever call hit that window — with enough calls in
# flight at once, that can silently zero out an entire chunk. These specific
# messages are the server recovering, not a malformed request, so they're
# worth the same retry budget as a timeout.
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
    if isinstance(exc, APITimeoutError | RateLimitError):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 500:
        return True
    return _is_model_unavailable(exc)


# Model-unavailable needs tens of seconds for a local reload; timeouts/5xx/rate
# limits usually clear much faster. Keep the long budget only for the former.
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
    """Wait out an open circuit without holding the concurrency semaphore."""
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
    """Return accumulated total_tokens for a job_id."""
    return _token_usage.get(job_id, 0)


def reset_token_usage(job_id: str | None = None) -> None:
    """Clear token usage counters (for tests)."""
    if job_id is None:
        _token_usage.clear()
    else:
        _token_usage.pop(job_id, None)


def reset_llm_client(concurrency: int | None = None) -> None:
    """Reset module-level client, semaphore, and circuit-breaker state (for tests)."""
    global _client, _semaphore, _circuit_lock, _unavailable_failures, _circuit_open_until
    _client = None
    _semaphore = asyncio.Semaphore(concurrency or settings.LLM_MAX_CONCURRENCY)
    _token_usage.clear()
    _circuit_lock = asyncio.Lock()
    _unavailable_failures = 0
    _circuit_open_until = 0.0


@retry(
    reraise=True,
    # 5 attempts. Model-unavailable keeps the original 2..30s exponential
    # (local reload). Other transients cap at 8s so a blip does not wait ~60s.
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
    # Hold the slot only for the network call. Tenacity's wait lives outside
    # this function, so a retry backoff must not occupy a concurrency slot.
    # The circuit wait is also outside the slot: parked callers don't block
    # a healthy in-flight request.
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
