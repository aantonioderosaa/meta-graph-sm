"""GDS nodeQueryGraph projection integration (Macrotask 2). Requires Docker."""

from __future__ import annotations

import pytest

from app.core import event_bus, neo4j_client
from app.db.schema import apply_schema_with_driver
from app.pipeline.dreaming import run_dreaming_pipeline
from app.pipeline.node_ppr_projection import (
    PPR_GRAPH_NAME,
    ensure_ppr_projection,
    refresh_ppr_projection,
)
from tests.neo4j_gds import neo4j_gds_container, wait_for_gds

CANON_ID = "alice-canon"
MERGED_ID = "alice-dup"
CONCEPT_ID = "concept-tech"


@pytest.fixture(scope="module")
def neo4j_container():
    container = neo4j_gds_container()
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
async def neo4j_ready(neo4j_container, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.NEO4J_URI", neo4j_container.get_connection_url())
    monkeypatch.setattr("app.core.config.settings.NEO4J_USER", neo4j_container.username)
    monkeypatch.setattr("app.core.config.settings.NEO4J_PASSWORD", neo4j_container.password)
    monkeypatch.setattr("app.core.config.settings.AUTO_MIGRATE", False)

    driver = neo4j_container.get_driver()
    wait_for_gds(driver)
    apply_schema_with_driver(driver)

    await neo4j_client.close_neo4j_driver()
    await neo4j_client.init_neo4j_driver()

    async_driver = neo4j_client.get_driver()
    async with async_driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
        await _drop_if_exists(session)

    yield neo4j_container

    async with async_driver.session() as session:
        await _drop_if_exists(session)
        await session.run("MATCH (n) DETACH DELETE n")
    await neo4j_client.close_neo4j_driver()


async def _drop_if_exists(session) -> None:
    result = await session.run(
        "CALL gds.graph.exists($name) YIELD exists RETURN exists",
        name=PPR_GRAPH_NAME,
    )
    record = await result.single()
    if record is not None and record["exists"]:
        await session.run("CALL gds.graph.drop($name, false)", name=PPR_GRAPH_NAME)


async def _graph_exists(session) -> bool:
    result = await session.run(
        "CALL gds.graph.exists($name) YIELD exists RETURN exists",
        name=PPR_GRAPH_NAME,
    )
    record = await result.single()
    return bool(record and record["exists"])


async def _projected_ids(session) -> set[str]:
    result = await session.run(
        """
        CALL gds.pageRank.stream($name)
        YIELD nodeId
        RETURN gds.util.asNode(nodeId).id AS id
        """,
        name=PPR_GRAPH_NAME,
    )
    ids: set[str] = set()
    async for record in result:
        if record["id"] is not None:
            ids.add(record["id"])
    return ids


async def _seed_nodes(session) -> None:
    await session.run(
        """
        CREATE (a:Node {id: $canon, name: 'Alice', type: 'entity', merged_into: null})
        CREATE (d:Node {id: $dup, name: 'Alice', type: 'entity', merged_into: $canon})
        CREATE (c:Concept {id: $concept, name: 'technology'})
        CREATE (a)-[:HAS_CONCEPT]->(c)
        """,
        canon=CANON_ID,
        dup=MERGED_ID,
        concept=CONCEPT_ID,
    )


@pytest.mark.asyncio
async def test_refresh_builds_projection_excluding_merged_nodes(neo4j_ready):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await _seed_nodes(session)
        await refresh_ppr_projection(session)
        assert await _graph_exists(session) is True
        ids = await _projected_ids(session)
        assert CANON_ID in ids
        assert CONCEPT_ID in ids
        assert MERGED_ID not in ids


@pytest.mark.asyncio
async def test_ensure_is_lazy_when_missing(neo4j_ready):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await _seed_nodes(session)
        assert await _graph_exists(session) is False
        await ensure_ppr_projection(session)
        assert await _graph_exists(session) is True
        await ensure_ppr_projection(session)
        assert await _graph_exists(session) is True


@pytest.mark.asyncio
async def test_dreaming_pipeline_refreshes_projection(neo4j_ready):
    event_bus.reset_event_bus()
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await _seed_nodes(session)
    await run_dreaming_pipeline("job-ppr-hook")
    async with driver.session() as session:
        assert await _graph_exists(session) is True
        ids = await _projected_ids(session)
        assert CANON_ID in ids
        assert MERGED_ID not in ids
    event_bus.reset_event_bus()
