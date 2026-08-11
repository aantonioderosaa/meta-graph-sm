"""FastAPI application entrypoint — Milestone 1."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import documents, dreaming, events, facts, graph, health, query, reconcile
from app.core.config import settings
from app.core.neo4j_client import close_neo4j_driver, init_neo4j_driver
from app.db.schema import apply_schema

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize Neo4j driver and optional schema bootstrap."""
    await init_neo4j_driver()
    if settings.AUTO_MIGRATE:
        try:
            count = await asyncio.to_thread(
                apply_schema,
                settings.NEO4J_URI,
                settings.NEO4J_USER,
                settings.NEO4J_PASSWORD,
            )
            logger.info("AUTO_MIGRATE applied %s schema statements", count)
        except Exception:
            logger.exception("AUTO_MIGRATE failed — is Neo4j reachable?")
            raise
    else:
        logger.info("AUTO_MIGRATE disabled — skipping schema bootstrap")
    yield
    await close_neo4j_driver()


app = FastAPI(title="Meta-Graph Facts Engine", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(events.router)
app.include_router(documents.router)
app.include_router(dreaming.router)
app.include_router(graph.router)
app.include_router(facts.router)
app.include_router(query.router)
app.include_router(reconcile.router)
