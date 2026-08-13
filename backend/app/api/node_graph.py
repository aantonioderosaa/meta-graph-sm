"""Node/Concept graph views (entities, events, participation, concepts)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.schemas import GraphResponse
from app.core.neo4j_client import Neo4jSessionDep
from app.pipeline import node_graph_engine

router = APIRouter(prefix="/graph", tags=["node-graph"])


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
) -> GraphResponse:
    """Return NVL-compatible entity graph (no event or participates edges)."""
    return await node_graph_engine.get_entity_graph(
        session,
        is_latest=is_latest,
        limit=limit,
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
) -> GraphResponse:
    """Return NVL-compatible event graph (no entity nodes)."""
    return await node_graph_engine.get_event_graph(
        session,
        is_latest=is_latest,
        limit=limit,
    )


@router.get("/participation", response_model=GraphResponse)
async def get_participation_graph_endpoint(
    session: Neo4jSessionDep,
    limit: int = Query(default=200, ge=1, le=1000),
) -> GraphResponse:
    """Return event→entity participates edges only."""
    return await node_graph_engine.get_participation_graph(session, limit=limit)


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
