"""Read-only Node/Concept graph views for the four frontend panels (Macrotask 6)."""

from __future__ import annotations

from typing import Any

from neo4j import AsyncSession

from app.api.schemas import GraphNode, GraphRelationship, GraphResponse

ENTITY_GRAPH_NODES_CYPHER = """
MATCH (n:Node {type:'entity'}) WHERE n.merged_into IS NULL
RETURN n LIMIT $limit
"""

ENTITY_GRAPH_RELS_CYPHER = """
MATCH (a:Node)-[r:Relation]->(b:Node)
WHERE a.id IN $ids AND b.id IN $ids
  AND a.type = 'entity' AND b.type = 'entity'
  AND ($is_latest IS NULL OR r.is_latest = $is_latest)
RETURN elementId(r) AS id, a.id AS from_id, b.id AS to_id, r.relation AS caption,
       coalesce(r.normalized_relation, r.relation) AS rel_type
"""

EVENT_GRAPH_NODES_CYPHER = """
MATCH (n:Node {type:'event'}) WHERE n.merged_into IS NULL
RETURN n LIMIT $limit
"""

EVENT_GRAPH_RELS_CYPHER = """
MATCH (a:Node)-[r:Relation]->(b:Node)
WHERE a.id IN $ids AND b.id IN $ids
  AND a.type = 'event' AND b.type = 'event'
  AND ($is_latest IS NULL OR r.is_latest = $is_latest)
RETURN elementId(r) AS id, a.id AS from_id, b.id AS to_id, r.relation AS caption,
       coalesce(r.normalized_relation, r.relation) AS rel_type
"""

ENTITY_GRAPH_CONCEPT_RELS_CYPHER = """
MATCH (a:Node {type:'entity'})-[:HAS_CONCEPT]->(c:Concept)
WHERE a.id IN $ids
RETURN a.id AS from_id, c.id AS concept_id, c.name AS concept_name
"""

EVENT_GRAPH_CONCEPT_RELS_CYPHER = """
MATCH (a:Node {type:'event'})-[:HAS_CONCEPT]->(c:Concept)
WHERE a.id IN $ids
RETURN a.id AS from_id, c.id AS concept_id, c.name AS concept_name
"""

PARTICIPATION_GRAPH_CYPHER = """
MATCH (ev:Node {type:'event'})-[r:Relation {normalized_relation:'participates'}]
      ->(e:Node {type:'entity'})
WHERE ev.merged_into IS NULL AND e.merged_into IS NULL
RETURN ev, r, e LIMIT $limit
"""

CONCEPT_OVERVIEW_CYPHER = """
MATCH (c:Concept)<-[:HAS_CONCEPT]-(n:Node)
WHERE n.merged_into IS NULL
RETURN c, count(n) AS degree
ORDER BY degree DESC LIMIT $limit
"""

CONCEPT_BY_ID_CYPHER = """
MATCH (c:Concept {id: $concept_id})
RETURN c
"""

CONCEPT_NEIGHBORS_CYPHER = """
MATCH (c:Concept {id: $concept_id})<-[:HAS_CONCEPT]-(n:Node)
WHERE n.merged_into IS NULL
RETURN n.id AS id, n.name AS name, n.type AS type
"""


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    getter = getattr(obj, "get", None)
    if callable(getter):
        return getter(key, default)
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return default


def _graph_node_from_node(n: Any, extra: dict[str, Any] | None = None) -> GraphNode:
    nid = str(n["id"])
    props: dict[str, Any] = {"type": _get(n, "type")}
    if extra:
        props.update(extra)
    return GraphNode(id=nid, caption=_get(n, "name") or nid, properties=props)


def _graph_relationship(
    rel_id: Any,
    from_id: Any,
    to_id: Any,
    rel_type: Any,
    caption: Any,
) -> GraphRelationship:
    type_value = rel_type or caption or "Relation"
    return GraphRelationship(
        id=str(rel_id),
        **{"from": str(from_id), "to": str(to_id)},
        type=str(type_value),
        caption=None if caption is None else str(caption),
    )


def _rel_id(rel: Any, from_id: str, to_id: str) -> str:
    element_id = getattr(rel, "element_id", None)
    if element_id is not None:
        return str(element_id)
    stored = _get(rel, "id")
    if stored is not None:
        return str(stored)
    return f"{from_id}->{to_id}"


async def _typed_node_graph(
    session: AsyncSession,
    *,
    nodes_cypher: str,
    rels_cypher: str,
    is_latest: bool | None,
    limit: int,
) -> GraphResponse:
    filter_latest = True if is_latest is True else None
    result = await session.run(nodes_cypher, limit=limit)
    nodes: list[GraphNode] = []
    ids: list[str] = []
    async for record in result:
        node = _graph_node_from_node(record["n"])
        nodes.append(node)
        ids.append(node.id)

    relationships: list[GraphRelationship] = []
    if ids:
        rel_result = await session.run(rels_cypher, ids=ids, is_latest=filter_latest)
        async for record in rel_result:
            relationships.append(
                _graph_relationship(
                    record["id"],
                    record["from_id"],
                    record["to_id"],
                    record["rel_type"],
                    record["caption"],
                )
            )

    return GraphResponse(nodes=nodes, relationships=relationships)


async def _append_concept_bridge(
    session: AsyncSession,
    base: GraphResponse,
    *,
    cypher: str,
    include_concepts: bool,
) -> GraphResponse:
    """Attach Concept nodes and HAS_CONCEPT edges scoped to `base` node ids."""
    if not include_concepts or not base.nodes:
        return base
    ids = [n.id for n in base.nodes]
    concept_rows = await session.run(cypher, ids=ids)
    concept_nodes: dict[str, GraphNode] = {}
    extra_rels: list[GraphRelationship] = []
    async for row in concept_rows:
        cid = str(row["concept_id"])
        concept_nodes.setdefault(
            cid,
            GraphNode(
                id=cid,
                caption=row["concept_name"] or cid,
                properties={"type": "concept"},
            ),
        )
        extra_rels.append(
            _graph_relationship(
                f"{row['from_id']}-HAS_CONCEPT-{cid}",
                row["from_id"],
                cid,
                "HAS_CONCEPT",
                "HAS_CONCEPT",
            )
        )
    return GraphResponse(
        nodes=base.nodes + list(concept_nodes.values()),
        relationships=base.relationships + extra_rels,
    )


async def get_entity_graph(
    session: AsyncSession,
    is_latest: bool | None = True,
    limit: int = 200,
    include_concepts: bool = False,
) -> GraphResponse:
    """Entity–entity graph. Relations among the fetched entity ids only."""
    base = await _typed_node_graph(
        session,
        nodes_cypher=ENTITY_GRAPH_NODES_CYPHER,
        rels_cypher=ENTITY_GRAPH_RELS_CYPHER,
        is_latest=is_latest,
        limit=limit,
    )
    return await _append_concept_bridge(
        session,
        base,
        cypher=ENTITY_GRAPH_CONCEPT_RELS_CYPHER,
        include_concepts=include_concepts,
    )


async def get_event_graph(
    session: AsyncSession,
    is_latest: bool | None = True,
    limit: int = 200,
    include_concepts: bool = False,
) -> GraphResponse:
    """Event–event graph. Relations among the fetched event ids only."""
    base = await _typed_node_graph(
        session,
        nodes_cypher=EVENT_GRAPH_NODES_CYPHER,
        rels_cypher=EVENT_GRAPH_RELS_CYPHER,
        is_latest=is_latest,
        limit=limit,
    )
    return await _append_concept_bridge(
        session,
        base,
        cypher=EVENT_GRAPH_CONCEPT_RELS_CYPHER,
        include_concepts=include_concepts,
    )


async def get_participation_graph(session: AsyncSession, limit: int = 200) -> GraphResponse:
    """Event → entity participates edges. Events without participants are omitted."""
    result = await session.run(PARTICIPATION_GRAPH_CYPHER, limit=limit)
    nodes_by_id: dict[str, GraphNode] = {}
    relationships: list[GraphRelationship] = []
    async for record in result:
        event_node = _graph_node_from_node(record["ev"])
        entity_node = _graph_node_from_node(record["e"])
        nodes_by_id.setdefault(event_node.id, event_node)
        nodes_by_id.setdefault(entity_node.id, entity_node)
        rel = record["r"]
        caption = _get(rel, "relation")
        rel_type = _get(rel, "normalized_relation") or caption
        relationships.append(
            _graph_relationship(
                _rel_id(rel, event_node.id, entity_node.id),
                event_node.id,
                entity_node.id,
                rel_type,
                caption,
            )
        )
    return GraphResponse(nodes=list(nodes_by_id.values()), relationships=relationships)


async def get_concept_overview(session: AsyncSession, limit: int = 100) -> GraphResponse:
    """Concepts ranked by number of linked (non-merged) nodes. No edges required."""
    result = await session.run(CONCEPT_OVERVIEW_CYPHER, limit=limit)
    nodes: list[GraphNode] = []
    async for record in result:
        concept = record["c"]
        nodes.append(
            _graph_node_from_node(
                concept,
                extra={"degree": int(record["degree"]), "type": "concept"},
            )
        )
    return GraphResponse(nodes=nodes, relationships=[])


async def get_concept_neighbors(session: AsyncSession, concept_id: str) -> GraphResponse:
    """Concept plus linked entities and events (HAS_CONCEPT from neighbor → concept)."""
    result = await session.run(CONCEPT_BY_ID_CYPHER, concept_id=concept_id)
    record = await result.single()
    if record is None:
        return GraphResponse(nodes=[], relationships=[])

    concept = record["c"]
    concept_node = _graph_node_from_node(concept, extra={"type": "concept"})
    nodes: list[GraphNode] = [concept_node]
    relationships: list[GraphRelationship] = []
    seen: set[str] = {concept_node.id}

    neigh_result = await session.run(CONCEPT_NEIGHBORS_CYPHER, concept_id=concept_id)
    async for rec in neigh_result:
        nid = str(rec["id"])
        if nid in seen:
            continue
        seen.add(nid)
        ntype = _get(rec, "type")
        name = _get(rec, "name")
        nodes.append(GraphNode(id=nid, caption=name or nid, properties={"type": ntype}))
        relationships.append(
            _graph_relationship(
                f"{nid}-HAS_CONCEPT-{concept_node.id}",
                nid,
                concept_node.id,
                "HAS_CONCEPT",
                "HAS_CONCEPT",
            )
        )

    return GraphResponse(nodes=nodes, relationships=relationships)
