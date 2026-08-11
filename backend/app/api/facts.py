"""Fact detail and history stubs (tech-spec §9, E2.5)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import (
    ChunkProvenance,
    FactDetailResponse,
    FactHistoryEntry,
    FactHistoryResponse,
)
from app.models.extraction import FactType

router = APIRouter(prefix="/facts", tags=["facts"])


@router.get("/{fact_id}", response_model=FactDetailResponse)
async def get_fact(fact_id: str) -> FactDetailResponse:
    """Stub: return a single fact with provenance."""
    return FactDetailResponse(
        id=fact_id,
        text="Alice works at Acme Corp.",
        type=FactType.fact,
        confidence=1.0,
        is_latest=True,
        created_at="2026-01-01T00:00:00Z",
        source_doc_id="doc-stub-1",
        provenance=[
            ChunkProvenance(
                chunk_id="chunk-stub-1",
                snippet="Alice joined Acme Corp last year.",
                doc_id="doc-stub-1",
            )
        ],
    )


@router.get("/{fact_id}/history", response_model=FactHistoryResponse)
async def get_fact_history(fact_id: str) -> FactHistoryResponse:
    """Stub: return an updates chain from newest to oldest."""
    return FactHistoryResponse(
        facts=[
            FactHistoryEntry(
                id=fact_id,
                text="Alice works at Acme Corp.",
                type=FactType.fact,
                is_latest=True,
                path_length=0,
            ),
            FactHistoryEntry(
                id="fact-historical",
                text="Alice worked at Beta Inc.",
                type=FactType.fact,
                is_latest=False,
                path_length=1,
            ),
        ]
    )
