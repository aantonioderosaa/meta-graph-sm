"""In-process SSE event bus keyed by job_id (tech-spec §10, E2.3)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def subscribe(job_id: str) -> asyncio.Queue[dict[str, Any]]:
    """Register a new subscriber queue for the given job_id."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    _subscribers.setdefault(job_id, []).append(queue)
    return queue


async def unsubscribe(job_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
    """Remove a subscriber queue; drop the job_id entry when empty."""
    queues = _subscribers.get(job_id)
    if not queues:
        return
    _subscribers[job_id] = [item for item in queues if item is not queue]
    if not _subscribers[job_id]:
        del _subscribers[job_id]


async def publish(
    job_id: str,
    stage: str,
    event: str,
    payload: dict[str, Any],
) -> None:
    """Publish an event to all subscribers of job_id."""
    message = {
        "ts": _now_iso(),
        "job_id": job_id,
        "stage": stage,
        "event": event,
        "payload": payload,
    }
    for queue in _subscribers.get(job_id, []):
        await queue.put(message)


def subscriber_count(job_id: str | None = None) -> int:
    """Return total subscriber queues, optionally scoped to one job_id."""
    if job_id is not None:
        return len(_subscribers.get(job_id, []))
    return sum(len(queues) for queues in _subscribers.values())


def reset_event_bus() -> None:
    """Clear all subscribers (for tests)."""
    _subscribers.clear()
