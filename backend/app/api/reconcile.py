"""is_latest reconciliation stub (tech-spec §9, E2.5)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import ReconcileResponse

router = APIRouter(prefix="/reconcile", tags=["reconcile"])


@router.post("", response_model=ReconcileResponse)
async def post_reconcile() -> ReconcileResponse:
    """Stub: run is_latest reconciliation and return drift count."""
    return ReconcileResponse(drift_count=0)
