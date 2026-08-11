"""Health check endpoint (E2.1)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.schemas import HealthResponse
from app.core.neo4j_client import Neo4jSessionDep

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(session: Neo4jSessionDep) -> HealthResponse:
    """Verify Neo4j connectivity and GDS availability."""
    try:
        result = await session.run("RETURN 1 AS n")
        record = await result.single()
        if record is None or record["n"] != 1:
            raise RuntimeError("Neo4j RETURN 1 failed")

        gds_result = await session.run("CALL gds.version() YIELD gdsVersion RETURN gdsVersion")
        gds_record = await gds_result.single()
        if gds_record is None:
            raise RuntimeError("GDS not loaded")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return HealthResponse(neo4j="ok", gds="ok")
