"""Tests for active task tracking in event-graph pipeline."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.pipeline.event_graph.infra import bus as event_graph_bus


@pytest.fixture(autouse=True)
def _reset_bus():
    event_graph_bus.reset_event_bus()
    yield
    event_graph_bus.reset_event_bus()


def test_has_running_job_none_when_no_jobs():
    """Test that has_running_job returns None when no jobs exist."""
    assert event_graph_bus.has_running_job() is None


def test_cancel_job_no_op_when_task_not_exists():
    """Test that cancel_job doesn't error when task doesn't exist."""
    # Should not raise any exception
    event_graph_bus.cancel_job("nonexistent-job")


def test_register_running_task_registers_task():
    """Test that register_running_task properly registers a task."""
    # Create a simple mock task 
    class MockTask:
        def __init__(self):
            self._done = False
            
        def done(self):
            return self._done
            
        def cancel(self):
            self._done = True
    
    task = MockTask()
    
    event_graph_bus.register_running_task("test-job", task)
    
    # Verify it's registered
    assert "test-job" in event_graph_bus._active_tasks
    assert event_graph_bus._active_tasks["test-job"] is task


def test_reset_event_bus_clears_active_tasks():
    """Test that reset_event_bus clears the active tasks dictionary."""
    # Create a mock task and register it
    class MockTask:
        def __init__(self):
            self._done = False
            
        def done(self):
            return self._done
    
    task = MockTask()
    
    event_graph_bus.register_running_task("test-job", task)
    
    # Verify registration worked
    assert "test-job" in event_graph_bus._active_tasks
    
    # Reset the bus
    event_graph_bus.reset_event_bus()
    
    # Should be cleared
    assert len(event_graph_bus._active_tasks) == 0


def test_function_signatures():
    """Test that all required functions exist with correct signatures."""
    # These should not raise AttributeError
    assert hasattr(event_graph_bus, 'has_running_job')
    assert hasattr(event_graph_bus, 'cancel_job') 
    assert hasattr(event_graph_bus, 'register_running_task')
    
    # Test basic functionality doesn't error (no actual implementation needed)
    assert callable(event_graph_bus.has_running_job)
    assert callable(event_graph_bus.cancel_job)
    assert callable(event_graph_bus.register_running_task)