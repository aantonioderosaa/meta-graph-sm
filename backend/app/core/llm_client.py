"""OpenAI structured-output wrapper with retry and concurrency (tech-spec §18, E2.4)."""

from __future__ import annotations

import asyncio
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

from app.core.config import settings

T = TypeVar("T", bound=BaseModel)

_semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)
_token_usage: dict[str, int] = {}
_client: AsyncOpenAI | None = None


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


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, APITimeoutError | RateLimitError):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status >= 500


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
    """Reset module-level client and semaphore state (for tests)."""
    global _client, _semaphore
    _client = None
    _semaphore = asyncio.Semaphore(concurrency or settings.LLM_MAX_CONCURRENCY)
    _token_usage.clear()


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception(_is_transient),
)
async def _call_openai(
    system_prompt: str,
    user_prompt: str,
    response_model: type[T],
    temperature: float,
    job_id: str | None,
) -> T:
    client = _get_client()
    completion = await client.beta.chat.completions.parse(
        model=settings.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=response_model,
        temperature=temperature,
    )
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
    async with _semaphore:
        try:
            return await _call_openai(
                system_prompt, user_prompt, response_model, temperature, job_id
            )
        except ValidationError as exc:
            raise LLMValidationError(str(exc)) from exc
