"""In-process SSE event bus for the event-graph pipeline (D6)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Must match ``run_event_graph_ingestion`` plus ancore (collocazione_temporale).
PIPELINE_STAGES = (
    "macro",
    "espansione",
    "collocazione_temporale",
    "relazioni",
    "riconciliazione",
    "done",
    "failed",
)

_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
_history: dict[str, list[dict[str, Any]]] = {}
_job_order: list[str] = []
_active_tasks: dict[str, asyncio.Task] = {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def register_job(job_id: str) -> None:
    """Ensure ``job_id`` appears in the live job list before the first event."""
    if not job_id or job_id in _history:
        return
    _history[job_id] = []
    _job_order.append(job_id)


def _documento_from_events(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        doc = payload.get("doc_id") or payload.get("documento")
        if doc:
            return str(doc)
    return None


def job_status(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        stage = event.get("stage")
        if stage == "failed":
            return "failed"
        if stage == "done":
            return "done"
    return "running"


def elenca_job() -> list[dict[str, Any]]:
    """In-memory ingest jobs, newest first, including event history for replay."""
    out: list[dict[str, Any]] = []
    for job_id in reversed(_job_order):
        events = list(_history.get(job_id, []))
        last = events[-1] if events else {}
        out.append(
            {
                "job_id": job_id,
                "status": job_status(events),
                "last_stage": last.get("stage"),
                "last_event": last.get("event"),
                "ts": last.get("ts"),
                "payload": last.get("payload") or {},
                "documento": _documento_from_events(events),
                "events": events,
            }
        )
    return out


def merge_job_lists(
    live: list[dict[str, Any]],
    runs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Prefer in-memory jobs (full SSE history); append completed :EventGraphRun."""
    seen = {str(item.get("job_id") or "") for item in live}
    out = list(live)
    for run in runs or []:
        job_id = str(run.get("id") or "")
        if not job_id or job_id in seen:
            continue
        ts = run.get("timestamp")
        documento = run.get("documento")
        payload = {
            key: value
            for key, value in run.items()
            if key not in {"id"}
        }
        if documento and "doc_id" not in payload:
            payload["doc_id"] = documento
        out.append(
            {
                "job_id": job_id,
                "status": "done",
                "last_stage": "done",
                "last_event": "pipeline_complete",
                "ts": ts,
                "payload": payload,
                "documento": documento,
                "events": [
                    {
                        "ts": ts,
                        "job_id": job_id,
                        "stage": "done",
                        "event": "pipeline_complete",
                        "payload": {"doc_id": documento},
                    }
                ],
            }
        )
        seen.add(job_id)
    return out


async def subscribe(job_id: str) -> asyncio.Queue[dict[str, Any]]:
    """Register a subscriber and replay recorded events (late join / reload)."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    history = list(_history.get(job_id, []))
    _subscribers.setdefault(job_id, []).append(queue)
    for message in history:
        queue.put_nowait(message)
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
    """Publish an event to all subscribers of job_id and keep it for replay."""
    message = {
        "ts": _now_iso(),
        "job_id": job_id,
        "stage": stage,
        "event": event,
        "payload": payload,
    }
    register_job(job_id)
    _history[job_id].append(message)
    for queue in _subscribers.get(job_id, []):
        await queue.put(message)


async def run_tracked_job(job_id: str, coro: Awaitable[Any]) -> None:
    """Run a background job; publish ``failed`` if it raises unhandled."""
    try:
        await coro
    except asyncio.CancelledError:
        # Handle cancelled tasks gracefully - this is expected behavior for cancellation
        logger.debug("job_id=%s was cancelled", job_id)
        # Don't publish failed event for intentional cancellations
        raise  # Re-raise to properly handle the cancellation
    except Exception as exc:
        logger.exception("job_id=%s failed with an unhandled exception", job_id)
        await publish(job_id, "failed", "pipeline_failed", {"error": str(exc)})


def subscriber_count(job_id: str | None = None) -> int:
    """Return total subscriber queues, optionally scoped to one job_id."""
    if job_id is not None:
        return len(_subscribers.get(job_id, []))
    return sum(len(queues) for queues in _subscribers.values())


def has_running_job() -> str | None:
    """Return the job_id of a running job, or None if no jobs are running.
    
    Uses the same logic as job_status to determine if a job is running.
    """
    for job_id in reversed(_job_order):
        events = list(_history.get(job_id, []))
        status = job_status(events)
        if status == "running":
            return job_id
    return None


async def cancel_job(job_id: str) -> None:
    """Cancel a running job by its ID.
    
    If the task exists and is not already completed/failed, it will be cancelled.
    No-op if no such task exists or if it's already finished.
    """
    task = _active_tasks.get(job_id)
    if task and not task.done():
        # Cancel the task properly
        task.cancel()
        try:
            # Wait for the cancellation to complete with a timeout
            await asyncio.wait_for(task, timeout=1.0)  # 1 second timeout
        except asyncio.TimeoutError:
            pass  # Task didn't finish in time but was cancelled
        except asyncio.CancelledError:
            pass  # Expected when task is cancelled


def register_running_task(job_id: str, task: asyncio.Task) -> None:
    """Register a running task for tracking.
    
    This should be called immediately after creating an asyncio task
    that is part of an ingest job.
    """
    _active_tasks[job_id] = task


def reset_event_bus() -> None:
    """Clear subscribers and job history (wipe / tests)."""
    _subscribers.clear()
    _history.clear()
    _job_order.clear()
    _active_tasks.clear()
