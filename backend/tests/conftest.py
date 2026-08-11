"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def configure_test_environment(monkeypatch, request):
    """Avoid Neo4j bootstrap during API/unit tests unless explicitly using Neo4j fixtures."""
    fixture_names = set(getattr(request, "fixturenames", ()))
    if fixture_names & {"neo4j_container", "neo4j_driver", "health_client"}:
        return

    monkeypatch.setattr("app.core.config.settings.AUTO_MIGRATE", False)

    async def noop_init() -> None:
        return None

    async def noop_close() -> None:
        return None

    monkeypatch.setattr("app.core.neo4j_client.init_neo4j_driver", noop_init)
    monkeypatch.setattr("app.core.neo4j_client.close_neo4j_driver", noop_close)
