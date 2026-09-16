"""FastAPI application entrypoint — event-graph branch only."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import event_graph as event_graph_api
from app.api.event_graph import event_graph_health
from app.pipeline.event_graph.config import settings
from app.pipeline.event_graph.infra.driver import (
    close_event_graph_driver,
    get_driver,
    init_event_graph_driver,
)
from app.pipeline.event_graph.infra.schema_bootstrap import ensure_event_graph_schema

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the event-graph driver, apply its schema, then close on shutdown."""
    await init_event_graph_driver()
    count = await ensure_event_graph_schema(get_driver())
    logger.info("Event-graph schema applied (%s statements)", count)
    yield
    await close_event_graph_driver()


app = FastAPI(title="Meta-Graph", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(event_graph_api.router)


@app.get("/health")
async def root_health() -> dict[str, str]:
    """Neo4j ping for Compose and legacy clients; same behavior as /event-graph/health."""
    return await event_graph_health()
