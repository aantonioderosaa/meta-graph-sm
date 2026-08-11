"""Graph explorer data endpoint (tech-spec §9, E5.4)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.schemas import GraphResponse
from app.core.neo4j_client import Neo4jSessionDep
from app.models.extraction import FactType
from app.pipeline import query_engine

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("", response_model=GraphResponse)
async def get_graph(
    session: Neo4jSessionDep,
    is_latest: bool = Query(
        default=True,
        description=(
            "If true, only current facts. If false, filter removed — include historical."
        ),
    ),
    fact_type: FactType | None = Query(default=None, alias="type"),
    doc_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> GraphResponse:
    """Return NVL-compatible {nodes, relationships} without Chunk nodes."""
    return await query_engine.get_graph(
        session,
        is_latest=is_latest,
        fact_type=fact_type,
        doc_id=doc_id,
        limit=limit,
    )
