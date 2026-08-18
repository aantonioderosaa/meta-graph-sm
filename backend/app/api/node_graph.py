"""Node/Concept graph views (entities, events, participation, concepts)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import (
    BundleResponse,
    GraphResetResponse,
    GraphResponse,
    NodeMetadataResponse,
)
from app.core.neo4j_client import Neo4jSessionDep
from app.pipeline import node_graph_engine

router = APIRouter(prefix="/graph", tags=["node-graph"])


@router.delete("", response_model=GraphResetResponse)
async def reset_graph(session: Neo4jSessionDep) -> GraphResetResponse:
    """Delete all nodes and relationships; leave constraints/indexes intact."""
    await session.run("MATCH (n) DETACH DELETE n")
    return GraphResetResponse(deleted=True)


@router.get("/entities", response_model=GraphResponse)
async def get_entity_graph_endpoint(
    session: Neo4jSessionDep,
    is_latest: bool = Query(
        default=True,
        description=(
            "If true, only current relations. If false, filter removed — include historical."
        ),
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    include_concepts: bool = Query(default=False),
) -> GraphResponse:
    """Return NVL-compatible entity graph (no event or participates edges)."""
    return await node_graph_engine.get_entity_graph(
        session,
        is_latest=is_latest,
        limit=limit,
        include_concepts=include_concepts,
    )


@router.get("/events", response_model=GraphResponse)
async def get_event_graph_endpoint(
    session: Neo4jSessionDep,
    is_latest: bool = Query(
        default=True,
        description=(
            "If true, only current relations. If false, filter removed — include historical."
        ),
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    include_concepts: bool = Query(default=False),
) -> GraphResponse:
    """Return NVL-compatible event graph (no entity nodes)."""
    return await node_graph_engine.get_event_graph(
        session,
        is_latest=is_latest,
        limit=limit,
        include_concepts=include_concepts,
    )


@router.get("/participation", response_model=GraphResponse)
async def get_participation_graph_endpoint(
    session: Neo4jSessionDep,
    limit: int = Query(default=200, ge=1, le=1000),
) -> GraphResponse:
    """Return event→entity participates edges only."""
    return await node_graph_engine.get_participation_graph(session, limit=limit)


@router.get("/macro", response_model=GraphResponse)
async def get_macro_graph_endpoint(
    session: Neo4jSessionDep,
    limit: int = Query(default=400, ge=1, le=2000),
) -> GraphResponse:
    """Aggregated Concept-domain view: names only, collapsed BUNDLE edges."""
    return await node_graph_engine.get_macro_graph(session, limit=limit)


@router.get("/bundle/{node_a_id}/{node_b_id}", response_model=BundleResponse)
async def get_graph_bundle_endpoint(
    node_a_id: str,
    node_b_id: str,
    session: Neo4jSessionDep,
) -> BundleResponse:
    """Expanded stored relations between two macro nodes (both directions)."""
    return await node_graph_engine.get_graph_bundle(session, node_a_id, node_b_id)


@router.get("/metadata/{node_id}", response_model=NodeMetadataResponse)
async def get_node_metadata_endpoint(
    node_id: str,
    session: Neo4jSessionDep,
) -> NodeMetadataResponse:
    """Concept or Node metadata for the macro drill-down panel (not NVL captions)."""
    data = await node_graph_engine.get_node_metadata(session, node_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    return data


@router.get("/concepts", response_model=GraphResponse)
async def get_concept_overview_endpoint(
    session: Neo4jSessionDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> GraphResponse:
    """Return concepts ranked by linked-node degree."""
    return await node_graph_engine.get_concept_overview(session, limit=limit)


@router.get("/concepts/{concept_id}", response_model=GraphResponse)
async def get_concept_neighbors_endpoint(
    concept_id: str,
    session: Neo4jSessionDep,
) -> GraphResponse:
    """Return a concept plus its linked entities and events (empty if missing)."""
    return await node_graph_engine.get_concept_neighbors(session, concept_id)
