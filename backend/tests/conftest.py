"""Shared pytest fixtures."""

from __future__ import annotations

import inspect

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "enable_node_extraction: run real process_chunk_node_extraction (disable autouse stub)",
    )


def as_batch_pair_extractor(pair):
    """Adapt a per-pair decision/mock into ``extract_pair_relations_batch``.

    Existing tests still pass a single ``PairRelationDecision``, an exception,
    or a per-pair callable; ingestion now calls the batch extractor.
    """
    from app.models.node_extraction import (
        PairIndexedDecision,
        PairRelationBatchResult,
        PairRelationDecision,
    )

    async def mock_batch(
        chunk_text: str,
        pairs: list[tuple[str, str, str, str]],
        job_id: str | None = None,
        corpus_summary: str = "",
    ) -> PairRelationBatchResult:
        if isinstance(pair, BaseException):
            raise pair
        if isinstance(pair, PairRelationBatchResult):
            return pair
        decisions: list[PairIndexedDecision] = []
        for index, (name_a, summary_a, name_b, summary_b) in enumerate(pairs):
            if callable(pair):
                item = pair(
                    chunk_text,
                    name_a,
                    summary_a,
                    name_b,
                    summary_b,
                    job_id=job_id,
                    corpus_summary=corpus_summary,
                )
                if inspect.isawaitable(item):
                    item = await item
            else:
                item = pair
            if isinstance(item, PairIndexedDecision):
                decisions.append(item.model_copy(update={"pair_index": index}))
            elif isinstance(item, PairRelationDecision):
                decisions.append(
                    PairIndexedDecision(pair_index=index, **item.model_dump())
                )
            else:
                raise TypeError(f"unsupported pair mock result: {type(item)!r}")
        return PairRelationBatchResult(decisions=decisions)

    return mock_batch


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
