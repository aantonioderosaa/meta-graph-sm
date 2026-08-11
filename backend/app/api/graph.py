"""Graph explorer data stub (tech-spec §9, E2.5)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.schemas import GraphNode, GraphRelationship, GraphResponse
from app.models.extraction import FactType

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("", response_model=GraphResponse)
async def get_graph(
    is_latest: bool = Query(default=True),
    fact_type: FactType | None = Query(default=None, alias="type"),
    doc_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> GraphResponse:
    """Stub: return NVL-compatible graph fixture."""
    _ = (is_latest, fact_type, doc_id, limit)
    nodes = [
        GraphNode(
            id="fact-a",
            caption="Alice works at Acme Corp.",
            properties={"type": "fact", "is_latest": True, "confidence": 1.0},
        ),
        GraphNode(
            id="fact-b",
            caption="Alice prefers remote work.",
            properties={"type": "preference", "is_latest": True, "confidence": 1.0},
        ),
    ]
    relationships = [
        GraphRelationship(
            id="rel-1",
            **{"from": "fact-b", "to": "fact-a"},
            type="EXTENDS",
            caption="extends",
        )
    ]
    return GraphResponse(nodes=nodes, relationships=relationships)
