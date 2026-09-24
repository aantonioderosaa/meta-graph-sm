"""Test for canceling jobs in the event-graph pipeline."""

import asyncio
import time
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport

from app.pipeline.event_graph.infra import bus as event_graph_bus


@pytest.fixture(autouse=True)
def _reset_bus():
    event_graph_bus.reset_event_bus()
    yield
    event_graph_bus.reset_event_bus()


@pytest.mark.asyncio
async def test_cancel_job_completes_quickly(monkeypatch):
    """Test that cancel_job returns quickly even when the task takes a long time."""
    
    # Create a mock slow task that sleeps for 5 seconds
    async def slow_task():
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            # This should be raised when we cancel the task
            raise
    
    # Mock get_driver to avoid database operations in tests
    monkeypatch.setattr(
        "app.api.event_graph.get_driver",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    
    # Start a fake job that will take a long time
    job_id = "test-slow-job"
    task = asyncio.create_task(slow_task())
    event_graph_bus.register_running_task(job_id, task)
    
    # Verify the job is running before cancellation
    assert event_graph_bus.has_running_job() == job_id
    
    # Measure how long cancellation takes
    start_time = time.time()
    await event_graph_bus.cancel_job(job_id)  # This should return quickly
    end_time = time.time()
    
    # Should complete within a short timeframe (much less than 5 seconds)
    assert end_time - start_time < 1.5, f"Cancellation took {end_time - start_time} seconds, should be under 1.5"
    
    # The task should now be cancelled
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_wipe_event_graph_cancels_running_job(monkeypatch):
    """Test that wipe_event_graph properly cancels a running job."""
    
    # Mock get_driver to avoid database operations in tests
    monkeypatch.setattr(
        "app.api.event_graph.get_driver",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    
    # Create a mock slow task
    async def slow_task():
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
    
    # Register the job and task
    job_id = "test-wipe-job"
    task = asyncio.create_task(slow_task())
    event_graph_bus.register_running_task(job_id, task)
    
    # Verify the job is running before cancellation
    assert event_graph_bus.has_running_job() == job_id
    
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Make sure we have a running job first
        response = await client.delete("/event-graph/graph")
        assert response.status_code == 200
        
        # Verify that the cancellation was handled correctly (no crash or hang)
        assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_cancel_nonexistent_job_noop():
    """Test that cancelling a non-existent job doesn't cause issues."""
    
    # Try to cancel a job that doesn't exist
    await event_graph_bus.cancel_job("nonexistent-job")
    # Should not raise any exception


@pytest.mark.asyncio
async def test_cancel_already_done_task():
    """Test cancellation of already completed task."""
    
    async def quick_task():
        return "done"
    
    # Create and run a quick task to completion
    job_id = "test-done-task"
    task = asyncio.create_task(quick_task())
    await task  # Wait for it to complete
    
    # Verify the task is done
    assert task.done()
    
    # Try to cancel it - should not raise any exception
    await event_graph_bus.cancel_job(job_id)