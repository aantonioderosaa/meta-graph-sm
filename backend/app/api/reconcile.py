"""is_latest reconciliation endpoint (tech-spec §7, §9, E4.6)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import ReconcileResponse
from app.pipeline.reconcile import reconcile

router = APIRouter(prefix="/reconcile", tags=["reconcile"])


@router.post("", response_model=ReconcileResponse)
async def post_reconcile() -> ReconcileResponse:
    """Run is_latest reconciliation and return drift count."""
    drift_count = await reconcile()
    return ReconcileResponse(drift_count=drift_count)
