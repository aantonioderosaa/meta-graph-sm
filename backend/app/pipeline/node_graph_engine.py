"""Read-only Node/Concept graph views for the four frontend panels (Macrotask 6).

Fase 6: subdomain fact visibility is a traversal of leaf ``:Relation`` edges
(see ``FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER``), never a duplicated Node–Node copy.
"""

from __future__ import annotations

from typing import Any

from neo4j import AsyncSession

from app.api.schemas import (
    BundleRelation,
    BundleResponse,
    GraphNode,
    GraphRelationship,
    GraphResponse,
    MetadataBreadcrumbItem,
    NodeMetadataResponse,
)
from app.pipeline.connectivity_rules import kernel_catch_all_ids
from app.pipeline.lca import (
    COUNT_PHYSICAL_LEAF_FACTS_CYPHER,
    FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER,
    WITNESSED_NEIGHBORS_CYPHER,
    count_physical_leaf_facts,
    facts_visible_in_subdomain,
    witnessed_neighbors,
)

__all__ = [
    "COUNT_PHYSICAL_LEAF_FACTS_CYPHER",
    "FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER",
    "WITNESSED_NEIGHBORS_CYPHER",
    "bundle_edge_id",
    "collapse_pair_counts",
    "count_physical_leaf_facts",
    "facts_visible_in_subdomain",
    "get_concept_neighbors",
    "get_concept_overview",
    "get_entity_graph",
    "get_event_graph",
    "get_graph_bundle",
    "get_macro_graph",
    "get_node_metadata",
    "get_participation_graph",
    "witnessed_neighbors",
]

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

CONCEPT_OVERVIEW_ISA_CYPHER = """
MATCH (child:Concept)-[:IS_A]->(parent:Concept)
WHERE child.id IN $ids OR parent.id IN $ids
RETURN child, parent
"""

CONCEPT_BY_ID_CYPHER = """
MATCH (c:Concept {id: $concept_id})
RETURN c
"""

CONCEPT_NEIGHBORS_CYPHER = """
MATCH (c:Concept {id: $concept_id})<-[:HAS_CONCEPT]-(n:Node)
WHERE n.merged_into IS NULL
RETURN n.id AS id, n.name AS name, n.type AS type,
       n.kernel_category AS kernel_category
"""

CONCEPT_ISA_PARENT_CYPHER = """
MATCH (c:Concept {id: $concept_id})-[:IS_A]->(parent:Concept)
RETURN parent
"""

CONCEPT_ISA_CHILDREN_CYPHER = """
MATCH (child:Concept)-[:IS_A]->(c:Concept {id: $concept_id})
RETURN child
"""

CONCEPT_MEMBERS_CYPHER = """
MATCH (n:Node)-[:MEMBER_OF]->(c:Concept {id: $concept_id})
WHERE n.merged_into IS NULL
RETURN n.id AS id, n.name AS name, n.type AS type,
       n.kernel_category AS kernel_category
"""

ENTITY_FACET_COUNTS_CYPHER = """
MATCH (n:Node)-[:SAME_AS]->(i:IdentityNode)
WHERE n.id IN $ids
MATCH (facet:Node)-[:SAME_AS]->(i)
WITH n.id AS id, count(DISTINCT facet) AS facet_count
WHERE facet_count > 1
RETURN id, facet_count
"""

_CONCEPT_PROP_KEYS = ("parent_uri", "kernel_category", "definition")


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
    for key in _CONCEPT_PROP_KEYS:
        value = _get(n, key)
        if value is not None:
            props[key] = value
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


async def _annotate_multi_facet_identities(
    session: AsyncSession, nodes: list[GraphNode]
) -> None:
    """Mark entity nodes that share an IdentityNode with more than one facet."""
    ids = [node.id for node in nodes]
    if not ids:
        return
    result = await session.run(ENTITY_FACET_COUNTS_CYPHER, ids=ids)
    counts: dict[str, int] = {}
    async for record in result:
        counts[str(record["id"])] = int(record["facet_count"])
    for node in nodes:
        count = counts.get(node.id)
        if count is not None and count > 1:
            node.properties["has_facets"] = True
            node.properties["facet_count"] = count


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
    await _annotate_multi_facet_identities(session, base.nodes)
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


def _neighbor_graph_node(rec: Any) -> GraphNode:
    nid = str(rec["id"])
    props: dict[str, Any] = {"type": _get(rec, "type")}
    kernel = _get(rec, "kernel_category")
    if kernel is not None:
        props["kernel_category"] = kernel
    return GraphNode(id=nid, caption=_get(rec, "name") or nid, properties=props)


async def get_concept_overview(session: AsyncSession, limit: int = 100) -> GraphResponse:
    """Concepts ranked by HAS_CONCEPT degree, plus IS_A child→parent edges."""
    result = await session.run(CONCEPT_OVERVIEW_CYPHER, limit=limit)
    nodes_by_id: dict[str, GraphNode] = {}
    async for record in result:
        concept = record["c"]
        node = _graph_node_from_node(
            concept,
            extra={"degree": int(record["degree"]), "type": "concept"},
        )
        nodes_by_id[node.id] = node

    relationships: list[GraphRelationship] = []
    ids = list(nodes_by_id.keys())
    if ids:
        isa_result = await session.run(CONCEPT_OVERVIEW_ISA_CYPHER, ids=ids)
        async for rec in isa_result:
            child_node = _graph_node_from_node(rec["child"], extra={"type": "concept"})
            parent_node = _graph_node_from_node(rec["parent"], extra={"type": "concept"})
            nodes_by_id.setdefault(child_node.id, child_node)
            nodes_by_id.setdefault(parent_node.id, parent_node)
            relationships.append(
                _graph_relationship(
                    f"{child_node.id}-IS_A-{parent_node.id}",
                    child_node.id,
                    parent_node.id,
                    "IS_A",
                    "IS_A",
                )
            )

    return GraphResponse(nodes=list(nodes_by_id.values()), relationships=relationships)


async def get_concept_neighbors(session: AsyncSession, concept_id: str) -> GraphResponse:
    """Concept plus HAS_CONCEPT neighbors, IS_A parent/children, MEMBER_OF members."""
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
        nodes.append(_neighbor_graph_node(rec))
        relationships.append(
            _graph_relationship(
                f"{nid}-HAS_CONCEPT-{concept_node.id}",
                nid,
                concept_node.id,
                "HAS_CONCEPT",
                "HAS_CONCEPT",
            )
        )

    parent_result = await session.run(CONCEPT_ISA_PARENT_CYPHER, concept_id=concept_id)
    async for rec in parent_result:
        parent_node = _graph_node_from_node(rec["parent"], extra={"type": "concept"})
        if parent_node.id not in seen:
            seen.add(parent_node.id)
            nodes.append(parent_node)
        relationships.append(
            _graph_relationship(
                f"{concept_node.id}-IS_A-{parent_node.id}",
                concept_node.id,
                parent_node.id,
                "IS_A",
                "IS_A",
            )
        )

    child_result = await session.run(CONCEPT_ISA_CHILDREN_CYPHER, concept_id=concept_id)
    async for rec in child_result:
        child_node = _graph_node_from_node(rec["child"], extra={"type": "concept"})
        if child_node.id not in seen:
            seen.add(child_node.id)
            nodes.append(child_node)
        relationships.append(
            _graph_relationship(
                f"{child_node.id}-IS_A-{concept_node.id}",
                child_node.id,
                concept_node.id,
                "IS_A",
                "IS_A",
            )
        )

    member_result = await session.run(CONCEPT_MEMBERS_CYPHER, concept_id=concept_id)
    async for rec in member_result:
        nid = str(rec["id"])
        if nid not in seen:
            seen.add(nid)
            nodes.append(_neighbor_graph_node(rec))
        relationships.append(
            _graph_relationship(
                f"{nid}-MEMBER_OF-{concept_node.id}",
                nid,
                concept_node.id,
                "MEMBER_OF",
                "MEMBER_OF",
            )
        )

    return GraphResponse(nodes=nodes, relationships=relationships)


# --- Fase 15: macro graph (names only) + bundle / metadata drill-down ---

MACRO_PROMOTED_CONCEPTS_CYPHER = """
MATCH (c:Concept {promoted: true})
RETURN c LIMIT $limit
"""

MACRO_UNPROMOTED_NODES_CYPHER = """
MATCH (n:Node)-[:MEMBER_OF]->(home:Concept)
WHERE n.merged_into IS NULL
  AND (
    home.id IN $kernel_ids
    OR (
      EXISTS {
        MATCH (home)-[:IS_A]->(k:Concept)
        WHERE k.id IN $kernel_ids
      }
      AND (home.promoted IS NULL OR home.promoted = false)
    )
  )
RETURN n LIMIT $limit
"""

MACRO_MEMBER_OF_CYPHER = """
MATCH (n:Node)-[:MEMBER_OF]->(c:Concept)
WHERE n.merged_into IS NULL
RETURN n.id AS node_id, c.id AS concept_id
"""

MACRO_LEAF_RELS_CYPHER = """
MATCH (a:Node)-[r:Relation]->(b:Node)
WHERE r.lifted_from IS NULL
  AND a.merged_into IS NULL
  AND b.merged_into IS NULL
RETURN a.id AS from_id, b.id AS to_id
"""

MACRO_ENDPOINT_IDS_CYPHER = """
OPTIONAL MATCH (n:Node {id: $id})
WHERE n.merged_into IS NULL
OPTIONAL MATCH (root:Concept {id: $id})
OPTIONAL MATCH (desc:Concept)-[:IS_A*0..]->(root)
OPTIONAL MATCH (inst:Node)-[:MEMBER_OF]->(desc)
WHERE inst IS NULL OR inst.merged_into IS NULL
WITH n, [xid IN collect(DISTINCT inst.id) WHERE xid IS NOT NULL] AS member_ids
RETURN CASE WHEN n IS NOT NULL THEN [n.id] ELSE member_ids END AS ids
"""

MACRO_BUNDLE_RELS_CYPHER = """
MATCH (a:Node)-[r:Relation]->(b:Node)
WHERE r.lifted_from IS NULL
  AND (
    (a.id IN $ids_a AND b.id IN $ids_b)
    OR (a.id IN $ids_b AND b.id IN $ids_a)
  )
RETURN elementId(r) AS id, a.id AS from_id, b.id AS to_id,
       r.relation AS relation,
       coalesce(r.normalized_relation, r.relation) AS rel_type,
       r.kernel_parent AS kernel_parent,
       r.witnesses_a AS witnesses_a,
       r.witnesses_b AS witnesses_b,
       r.provenance AS provenance,
       r.valid_time AS valid_time,
       r.system_time AS system_time
"""

MACRO_METADATA_RESOLVE_CYPHER = """
OPTIONAL MATCH (c:Concept {id: $id})
OPTIONAL MATCH (n:Node {id: $id})
WHERE n.merged_into IS NULL
RETURN c, n
"""

MACRO_CONCEPT_BREADCRUMB_CYPHER = """
MATCH (c:Concept {id: $id})
OPTIONAL MATCH path = (c)-[:IS_A*0..8]->(anc:Concept)
RETURN c, anc, length(path) AS dist
ORDER BY dist
"""

MACRO_MEMBER_COUNT_CYPHER = """
MATCH (n:Node)-[:MEMBER_OF]->(c:Concept {id: $id})
WHERE n.merged_into IS NULL
RETURN count(n) AS n
"""

MACRO_NODE_IDENTITIES_CYPHER = """
MATCH (n:Node {id: $id})-[:SAME_AS]->(i:IdentityNode)
RETURN i.uri AS uri
"""

_NODE_ATTR_SKIP = frozenset(
    {
        "id",
        "name",
        "type",
        "summary",
        "kernel_category",
        "embedding",
        "summary_embedding",
        "merged_into",
    }
)


def bundle_edge_id(node_a_id: str, node_b_id: str) -> str:
    """Stable undirected NVL id: ``bundle:{from}:{to}`` with ``from < to``."""
    left, right = sorted((str(node_a_id), str(node_b_id)))
    return f"bundle:{left}:{right}"


def collapse_pair_counts(
    pairs: list[tuple[str, str]],
    macro_ids: set[str],
    homes: dict[str, str],
) -> dict[tuple[str, str], int]:
    """Map leaf ``:Relation`` endpoints onto macro nodes and count undirected pairs."""

    def to_macro(nid: str) -> str | None:
        if nid in macro_ids:
            return nid
        home = homes.get(nid)
        if home in macro_ids:
            return home
        return None

    counts: dict[tuple[str, str], int] = {}
    for src, tgt in pairs:
        left = to_macro(str(src))
        right = to_macro(str(tgt))
        if left is None or right is None or left == right:
            continue
        key = (left, right) if left < right else (right, left)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    try:
        return [str(item) for item in value if str(item).strip()]
    except TypeError:
        return [str(value)] if str(value).strip() else []


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    iso = getattr(value, "iso_format", None)
    if callable(iso):
        return str(iso())
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return str(iso())
    text = str(value).strip()
    return text or None


def _node_attributes(n: Any) -> dict[str, Any]:
    items = getattr(n, "items", None)
    props = getattr(n, "_props", None)
    if callable(items):
        raw = dict(items())
    elif isinstance(n, dict):
        raw = dict(n)
    elif isinstance(props, dict):
        raw = dict(props)
    else:
        raw = {}
    return {key: value for key, value in raw.items() if key not in _NODE_ATTR_SKIP}


async def get_macro_graph(session: AsyncSession, limit: int = 400) -> GraphResponse:
    """Promoted concepts + first-level leftover nodes; collapsed BUNDLE edges."""
    kernel_ids = list(kernel_catch_all_ids())
    nodes_by_id: dict[str, GraphNode] = {}

    promoted = await session.run(MACRO_PROMOTED_CONCEPTS_CYPHER, limit=limit)
    async for record in promoted:
        node = _graph_node_from_node(record["c"], extra={"type": "concept"})
        nodes_by_id[node.id] = node

    leftovers = await session.run(
        MACRO_UNPROMOTED_NODES_CYPHER, kernel_ids=kernel_ids, limit=limit
    )
    async for record in leftovers:
        node = _graph_node_from_node(record["n"])
        nodes_by_id.setdefault(node.id, node)

    if not nodes_by_id:
        return GraphResponse(nodes=[], relationships=[])

    homes: dict[str, str] = {}
    home_result = await session.run(MACRO_MEMBER_OF_CYPHER)
    async for record in home_result:
        homes[str(record["node_id"])] = str(record["concept_id"])

    pairs: list[tuple[str, str]] = []
    rel_result = await session.run(MACRO_LEAF_RELS_CYPHER)
    async for record in rel_result:
        pairs.append((str(record["from_id"]), str(record["to_id"])))

    counts = collapse_pair_counts(pairs, set(nodes_by_id), homes)
    relationships = [
        _graph_relationship(
            bundle_edge_id(left, right),
            left,
            right,
            "BUNDLE",
            str(count),
        )
        for (left, right), count in sorted(counts.items())
    ]
    return GraphResponse(nodes=list(nodes_by_id.values()), relationships=relationships)


async def _endpoint_instance_ids(session: AsyncSession, endpoint_id: str) -> list[str]:
    result = await session.run(MACRO_ENDPOINT_IDS_CYPHER, id=endpoint_id)
    record = await result.single()
    if record is None:
        return []
    ids = record["ids"] or []
    return [str(item) for item in ids]


async def get_graph_bundle(
    session: AsyncSession, node_a_id: str, node_b_id: str
) -> BundleResponse:
    """Stored ``:Relation`` rows between two macro endpoints (both directions)."""
    ids_a = await _endpoint_instance_ids(session, node_a_id)
    ids_b = await _endpoint_instance_ids(session, node_b_id)
    if not ids_a or not ids_b:
        return BundleResponse(items=[])

    result = await session.run(MACRO_BUNDLE_RELS_CYPHER, ids_a=ids_a, ids_b=ids_b)
    items: list[BundleRelation] = []
    async for record in result:
        caption = _get(record, "relation")
        rel_type = _get(record, "rel_type") or caption or "Relation"
        kernel_parent = _get(record, "kernel_parent")
        items.append(
            BundleRelation(
                id=str(record["id"]),
                **{"from": str(record["from_id"]), "to": str(record["to_id"])},
                type=str(rel_type),
                relation=None if caption is None else str(caption),
                kernel_parent=None if kernel_parent is None else str(kernel_parent),
                witnesses_a=_as_str_list(_get(record, "witnesses_a")),
                witnesses_b=_as_str_list(_get(record, "witnesses_b")),
                provenance=_get(record, "provenance"),
                valid_time=_as_iso(_get(record, "valid_time")),
                system_time=_as_iso(_get(record, "system_time")),
                epistemic_status="asserted",
            )
        )
    return BundleResponse(items=items)


def _breadcrumb_from_rows(
    concept: Any, rows: list[dict[str, Any]]
) -> list[MetadataBreadcrumbItem]:
    items: list[MetadataBreadcrumbItem] = []
    seen: set[str] = set()
    kernel_ids = kernel_catch_all_ids()
    ordered = sorted(rows, key=lambda row: int(row.get("dist") or 0))
    for row in ordered:
        anc = row.get("anc") or concept
        nid = str(_get(anc, "id") or "")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        items.append(
            MetadataBreadcrumbItem(
                id=nid,
                name=str(_get(anc, "name") or nid),
                kernel_category=_get(anc, "kernel_category"),
            )
        )
        if nid in kernel_ids:
            break
    if not items:
        cid = str(_get(concept, "id"))
        items.append(
            MetadataBreadcrumbItem(
                id=cid,
                name=str(_get(concept, "name") or cid),
                kernel_category=_get(concept, "kernel_category"),
            )
        )
    return items


async def get_node_metadata(
    session: AsyncSession, node_id: str
) -> NodeMetadataResponse | None:
    result = await session.run(MACRO_METADATA_RESOLVE_CYPHER, id=node_id)
    record = await result.single()
    if record is None:
        return None
    concept = record["c"]
    node = record["n"]
    if concept is not None:
        crumb_result = await session.run(MACRO_CONCEPT_BREADCRUMB_CYPHER, id=node_id)
        rows: list[dict[str, Any]] = []
        async for row in crumb_result:
            rows.append({"anc": row["anc"], "dist": row["dist"]})
        count_result = await session.run(MACRO_MEMBER_COUNT_CYPHER, id=node_id)
        count_row = await count_result.single()
        member_count = int(count_row["n"]) if count_row is not None else 0
        cid = str(_get(concept, "id"))
        return NodeMetadataResponse(
            id=cid,
            kind="concept",
            name=str(_get(concept, "name") or cid),
            kernel_category=_get(concept, "kernel_category"),
            definition=_get(concept, "definition"),
            aliases=_as_str_list(_get(concept, "aliases")),
            is_a_breadcrumb=_breadcrumb_from_rows(concept, rows),
            member_count=member_count,
        )
    if node is not None:
        ident_result = await session.run(MACRO_NODE_IDENTITIES_CYPHER, id=node_id)
        uris: list[str] = []
        async for row in ident_result:
            uri = row["uri"]
            if uri:
                uris.append(str(uri))
        nid = str(_get(node, "id"))
        return NodeMetadataResponse(
            id=nid,
            kind="node",
            name=str(_get(node, "name") or nid),
            kernel_category=_get(node, "kernel_category"),
            summary=_get(node, "summary"),
            attributes=_node_attributes(node),
            identity_uris=uris,
        )
    return None

