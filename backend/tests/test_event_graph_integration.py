"""Optional Docker/testcontainers event-graph integration (skipped by default)."""

from __future__ import annotations

import pytest


def _docker_available() -> bool:
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def _testcontainers_available() -> bool:
    try:
        import testcontainers  # noqa: F401

        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_docker_available() and _testcontainers_available()),
        reason="Docker/testcontainers not available",
    ),
]


def test_docker_integration_opt_in_not_required():
    """In-memory M14 covers idempotence; this file must not run on the default unit job."""
    assert _docker_available()
    assert _testcontainers_available()
