"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "enable_node_extraction: run real process_chunk_node_extraction (disable autouse stub)",
    )


@pytest.fixture(autouse=True)
def configure_test_environment(monkeypatch, request):
    """Avoid Neo4j bootstrap during API/unit tests unless explicitly using Neo4j fixtures."""
    fixture_names = set(getattr(request, "fixturenames", ()))
    if fixture_names & {
        "neo4j_container",
        "neo4j_driver",
        "neo4j_ready",
        "health_client",
    }:
        return

    monkeypatch.setattr("app.core.config.settings.AUTO_MIGRATE", False)

    async def noop_init() -> None:
        return None

    async def noop_close() -> None:
        return None

    monkeypatch.setattr("app.core.neo4j_client.init_neo4j_driver", noop_init)
    monkeypatch.setattr("app.core.neo4j_client.close_neo4j_driver", noop_close)


@pytest.fixture(autouse=True)
def stub_node_extraction_unless_enabled(monkeypatch, request):
    """Keep existing ingestion tests off the OpenAI entity/event extractors."""
    if request.node.get_closest_marker("enable_node_extraction"):
        return

    async def _noop(session, chunk, doc_id, job_id, corpus_summary: str = "") -> int:
        _ = session, chunk, doc_id, job_id, corpus_summary
        return 0

    monkeypatch.setattr(
        "app.pipeline.ingestion.process_chunk_node_extraction",
        _noop,
        raising=False,
    )
