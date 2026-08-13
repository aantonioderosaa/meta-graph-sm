"""Unit tests for scoped :Relation is_latest reconciliation (Macrotask 5.1). No Docker."""

from __future__ import annotations

import pytest

from app.pipeline.reconcile import (
    RECONCILE_SCOPED_RELATIONS_CYPHER,
    reconcile_scoped_relations,
)


def _compact(cypher: str) -> str:
    return " ".join(cypher.split())


@pytest.mark.asyncio
async def test_empty_node_ids_returns_zero_without_session(monkeypatch):
    def boom_driver():
        raise AssertionError("get_driver must not run when node_ids is empty")

    monkeypatch.setattr("app.pipeline.reconcile.get_driver", boom_driver)

    assert await reconcile_scoped_relations([]) == 0


def test_cypher_is_scoped_by_node_ids_and_mentions_updates():
    cypher = RECONCILE_SCOPED_RELATIONS_CYPHER
    compact = _compact(cypher)
    assert "$node_ids" in cypher
    assert "a.id IN $node_ids OR b.id IN $node_ids" in compact
    assert "updates" in cypher
    assert "normalized_relation = 'updates'" in compact
    assert "is_latest" in cypher
    assert "MATCH ()-[r:Relation]->()" not in compact
    assert "MATCH (n:Node) RETURN n" not in compact


@pytest.mark.asyncio
async def test_scoped_run_passes_node_ids(monkeypatch):
    recorded: list[tuple[str, dict]] = []

    class FakeResult:
        async def single(self):
            return {"driftCount": 3}

    class FakeSession:
        async def run(self, cypher, **kwargs):
            recorded.append((cypher, kwargs))
            return FakeResult()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeDriver:
        def session(self):
            return FakeSession()

    monkeypatch.setattr("app.pipeline.reconcile.get_driver", lambda: FakeDriver())

    count = await reconcile_scoped_relations(["n1", "n2"])

    assert count == 3
    assert len(recorded) == 1
    cypher, kwargs = recorded[0]
    assert cypher == RECONCILE_SCOPED_RELATIONS_CYPHER
    assert kwargs["node_ids"] == ["n1", "n2"]
