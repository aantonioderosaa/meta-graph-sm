"""Unit tests for the Node-query GDS projection (Macrotask 2). No Docker."""

from __future__ import annotations

import pytest

from app.pipeline.node_ppr_projection import (
    EXISTS_CYPHER,
    NODE_QUERY,
    PPR_GRAPH_NAME,
    PROJECT_CYPHER,
    REL_QUERY,
    ensure_ppr_projection,
    refresh_ppr_projection,
)


class FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    async def single(self):
        return self._records[0] if self._records else None

    async def consume(self):
        return None


class FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.queue: list[list[dict]] = []

    def enqueue(self, records: list[dict]) -> None:
        self.queue.append(records)

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        records = self.queue.pop(0) if self.queue else []
        return FakeResult(records)


def test_projection_queries_exclude_merged_nodes():
    compact_nodes = " ".join(NODE_QUERY.split())
    assert "merged_into IS NULL" in NODE_QUERY
    assert "Concept" in NODE_QUERY
    assert "Relation|HAS_CONCEPT" in REL_QUERY
    assert "merged_into IS NULL" in REL_QUERY
    assert "UNION" in REL_QUERY
    compact_rels = " ".join(REL_QUERY.split())
    assert "id(b) AS source, id(a) AS target" in compact_rels
    assert "MATCH (n:Node) RETURN n" not in compact_nodes


@pytest.mark.asyncio
async def test_refresh_drops_then_projects():
    session = FakeSession()
    await refresh_ppr_projection(session)
    assert len(session.calls) == 2
    drop_cypher, drop_kw = session.calls[0]
    project_cypher, project_kw = session.calls[1]
    assert "gds.graph.drop" in drop_cypher
    assert drop_kw["name"] == PPR_GRAPH_NAME
    assert project_cypher == PROJECT_CYPHER
    assert project_kw["name"] == PPR_GRAPH_NAME
    assert project_kw["nodeQuery"] == NODE_QUERY
    assert project_kw["relQuery"] == REL_QUERY


@pytest.mark.asyncio
async def test_ensure_skips_rebuild_when_graph_exists():
    session = FakeSession()
    session.enqueue([{"exists": True}])
    await ensure_ppr_projection(session)
    assert len(session.calls) == 1
    cypher, kwargs = session.calls[0]
    assert cypher == EXISTS_CYPHER
    assert kwargs["name"] == PPR_GRAPH_NAME
    assert not any("project.cypher" in call[0] for call in session.calls)


@pytest.mark.asyncio
async def test_refresh_swallows_missing_gds_procedure():
    from neo4j.exceptions import ClientError

    class BoomSession(FakeSession):
        async def run(self, cypher, **kwargs):
            self.calls.append((cypher, kwargs))
            raise ClientError("There is no procedure with the name `gds.graph.drop`")

    session = BoomSession()
    await refresh_ppr_projection(session)
    assert any("gds.graph.drop" in cy for cy, _kw in session.calls)


@pytest.mark.asyncio
async def test_ensure_rebuilds_when_missing():
    session = FakeSession()
    session.enqueue([{"exists": False}])
    await ensure_ppr_projection(session)
    assert session.calls[0][0] == EXISTS_CYPHER
    assert any("gds.graph.drop" in call[0] for call in session.calls)
    assert any("project.cypher" in call[0] for call in session.calls)
