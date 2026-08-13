"""GDS in-memory projection for Personalized PageRank on the Node/Concept layer.

Rebuilt once per dreaming cycle (background job), never on the synchronous
query path. Native ``gds.graph.project`` cannot load string properties such as
``merged_into``; Cypher projection filters first so no node properties are
loaded.
"""

from __future__ import annotations

import logging

from neo4j import AsyncSession
from neo4j.exceptions import ClientError

logger = logging.getLogger(__name__)

PPR_GRAPH_NAME = "nodeQueryGraph"

NODE_QUERY = """
MATCH (n) WHERE (n:Node AND n.merged_into IS NULL) OR n:Concept
RETURN id(n) AS id
"""

REL_QUERY = """
MATCH (a)-[r:Relation|HAS_CONCEPT]->(b)
WHERE (a:Node AND a.merged_into IS NULL OR a:Concept)
  AND (b:Node AND b.merged_into IS NULL OR b:Concept)
RETURN id(a) AS source, id(b) AS target
"""

DROP_CYPHER = """
CALL gds.graph.drop($name, false)
YIELD graphName
RETURN graphName
"""

PROJECT_CYPHER = """
CALL gds.graph.project.cypher($name, $nodeQuery, $relQuery)
YIELD graphName
RETURN graphName
"""

EXISTS_CYPHER = "CALL gds.graph.exists($name) YIELD exists RETURN exists"


async def refresh_ppr_projection(session: AsyncSession) -> None:
    """Drop + rebuild the GDS in-memory projection used by Personalized PageRank.

    Cost is proportional to total graph size — paid once per dreaming cycle
    (background job), never inside a synchronous user question. An empty
    Node/Concept store cannot be projected by GDS; that case is a no-op so a
    virgin deploy does not fail dreaming.
    """
    drop = await session.run(DROP_CYPHER, name=PPR_GRAPH_NAME)
    await drop.consume()
    try:
        result = await session.run(
            PROJECT_CYPHER,
            name=PPR_GRAPH_NAME,
            nodeQuery=NODE_QUERY,
            relQuery=REL_QUERY,
        )
        await result.consume()
    except ClientError:
        logger.warning(
            "PPR projection skipped: no Node/Concept nodes to project",
            exc_info=True,
        )


async def ensure_ppr_projection(session: AsyncSession) -> None:
    """Lazy build-on-first-use if no dreaming cycle has run yet."""
    result = await session.run(EXISTS_CYPHER, name=PPR_GRAPH_NAME)
    record = await result.single()
    if record is None or not record["exists"]:
        await refresh_ppr_projection(session)
