"""Fresh-fact grouping via GDS kNN + WCC (tech-spec §6.1, E4.1).

GDS API choice (Neo4j GDS 2.12): ``gds.graph.project.cypher`` projects only
undreamed ``Fact`` nodes (optional ``doc_id`` filter) because native
``gds.graph.project`` in 2.12 rejects ``nodeFilter`` and empty rel projections.
We then run ``gds.knn.mutate`` to add in-memory ``SIMILAR`` edges and
``gds.wcc.stream`` for components. Tech-spec §6.1 shows ``gds.knn.write`` +
``gds.wcc.stream`` on a ``'*'`` projection; ``mutate`` + ``stream`` is the
equivalent pattern on GDS 2.12.

The projected graph ``freshFacts`` is always dropped in a ``finally`` block via
``gds.graph.drop('freshFacts', false)``, even when kNN/WCC raises.
"""

from __future__ import annotations

import logging

from neo4j import AsyncSession

from app.core.neo4j_client import get_driver

logger = logging.getLogger(__name__)

GRAPH_NAME = "freshFacts"

EMPTY_REL_QUERY = (
    "MATCH (a:Fact)-[r:SIMILAR]->(b:Fact) WHERE false "
    "RETURN id(a) AS source, id(b) AS target"
)

KNN_MUTATE_CYPHER = """
CALL gds.knn.mutate($graph_name, {
  nodeProperties: ['embedding'],
  topK: 10,
  similarityCutoff: 0.80,
  mutateRelationshipType: 'SIMILAR',
  mutateProperty: 'score'
})
YIELD relationshipsWritten
RETURN relationshipsWritten
"""

WCC_STREAM_CYPHER = """
CALL gds.wcc.stream($graph_name, { relationshipTypes: ['SIMILAR'] })
YIELD nodeId, componentId
WITH componentId, gds.util.asNode(nodeId) AS node
RETURN componentId, collect(node.id) AS factIds
ORDER BY componentId
"""

DROP_GRAPH_CYPHER = """
CALL gds.graph.drop($graph_name, false)
YIELD graphName
RETURN graphName
"""


def _build_node_query(doc_id: str | None) -> str:
    if doc_id is None:
        return (
            "MATCH (n:Fact) WHERE n.dreamed = false "
            "RETURN id(n) AS id, n.embedding AS embedding"
        )
    escaped = doc_id.replace("\\", "\\\\").replace("'", "\\'")
    return (
        "MATCH (n:Fact) WHERE n.dreamed = false "
        f"AND n.source_doc_id = '{escaped}' "
        "RETURN id(n) AS id, n.embedding AS embedding"
    )


async def _drop_projected_graph(session: AsyncSession) -> None:
    result = await session.run(
        "CALL gds.graph.exists($graph_name) YIELD exists RETURN exists",
        graph_name=GRAPH_NAME,
    )
    record = await result.single()
    if record is not None and record["exists"]:
        await session.run(DROP_GRAPH_CYPHER, graph_name=GRAPH_NAME)


async def _fetch_all_fresh_fact_ids(session: AsyncSession, doc_id: str | None) -> list[str]:
    if doc_id is None:
        cypher = "MATCH (n:Fact) WHERE n.dreamed = false RETURN collect(n.id) AS factIds"
        params: dict[str, str] = {}
    else:
        cypher = (
            "MATCH (n:Fact) WHERE n.dreamed = false AND n.source_doc_id = $doc_id "
            "RETURN collect(n.id) AS factIds"
        )
        params = {"doc_id": doc_id}
    result = await session.run(cypher, **params)
    record = await result.single()
    if record is None:
        return []
    return list(record["factIds"])


async def group_fresh_facts(doc_id: str | None = None) -> list[list[str]]:
    """Group undreamed facts by embedding similarity (kNN cutoff 0.80) + WCC."""
    node_query = _build_node_query(doc_id)
    project_cypher = """
    CALL gds.graph.project.cypher(
      $graph_name,
      $node_query,
      $relationship_query
    )
    YIELD graphName
    RETURN graphName
    """
    driver = get_driver()

    async with driver.session() as session:
        try:
            await _drop_projected_graph(session)
            await session.run(
                project_cypher,
                graph_name=GRAPH_NAME,
                node_query=node_query,
                relationship_query=EMPTY_REL_QUERY,
            )
            await session.run(KNN_MUTATE_CYPHER, graph_name=GRAPH_NAME)

            result = await session.run(WCC_STREAM_CYPHER, graph_name=GRAPH_NAME)
            component_map: dict[int, list[str]] = {}
            async for record in result:
                component_map[int(record["componentId"])] = list(record["factIds"])

            all_ids = await _fetch_all_fresh_fact_ids(session, doc_id)
            grouped_ids: set[str] = set()
            groups: list[list[str]] = []
            for fact_ids in component_map.values():
                if fact_ids:
                    groups.append(fact_ids)
                    grouped_ids.update(fact_ids)

            for fact_id in all_ids:
                if fact_id not in grouped_ids:
                    groups.append([fact_id])

            return groups
        finally:
            try:
                await _drop_projected_graph(session)
            except Exception:
                logger.exception("failed to drop GDS graph %s", GRAPH_NAME)
                raise
