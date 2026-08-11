"""FastAPI application entrypoint — Milestone 1."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from app.db.schema import apply_schema

logger = logging.getLogger(__name__)

app = FastAPI(title="Meta-Graph Facts Engine", version="0.1.0")


@app.on_event("startup")
async def migrate_schema_on_startup() -> None:
    """Apply Neo4j schema when AUTO_MIGRATE is enabled (default true in dev)."""
    flag = os.getenv("AUTO_MIGRATE", "true").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        logger.info("AUTO_MIGRATE disabled — skipping schema bootstrap")
        return
    try:
        count = apply_schema()
        logger.info("AUTO_MIGRATE applied %s schema statements", count)
    except Exception:
        logger.exception("AUTO_MIGRATE failed — is Neo4j reachable?")
        raise


@app.get("/health")
async def health() -> dict[str, str]:
    """Placeholder health check — replaced with Neo4j+GDS probe in Epic 2."""
    return {"status": "not_implemented"}
