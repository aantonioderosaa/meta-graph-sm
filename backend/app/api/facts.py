"""Fact detail and history endpoints (tech-spec §9, E5.1–E5.2)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.schemas import FactDetailResponse, FactHistoryResponse
from app.core.neo4j_client import Neo4jSessionDep
from app.pipeline import query_engine

router = APIRouter(prefix="/facts", tags=["facts"])


@router.get("/{fact_id}", response_model=FactDetailResponse)
async def get_fact(fact_id: str, session: Neo4jSessionDep) -> FactDetailResponse:
    """Return full fact detail with chunk provenance."""
    detail = await query_engine.get_fact_detail(session, fact_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Fact '{fact_id}' not found")
    return detail


@router.get("/{fact_id}/history", response_model=FactHistoryResponse)
async def get_fact_history(fact_id: str, session: Neo4jSessionDep) -> FactHistoryResponse:
    """Return UPDATES chain from newest to oldest (tech-spec §8.2)."""
    history = await query_engine.get_fact_history(session, fact_id)
    if history is None:
        raise HTTPException(status_code=404, detail=f"Fact '{fact_id}' not found")
    return history
