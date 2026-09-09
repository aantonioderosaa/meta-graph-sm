"""Async Neo4j driver lifecycle for the event-graph package (D6)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession

from app.pipeline.event_graph.config import settings

_driver: AsyncDriver | None = None


async def init_event_graph_driver() -> None:
    """Create the package-local async driver (call on application startup)."""
    global _driver
    if _driver is not None:
        return
    _driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )


async def close_event_graph_driver() -> None:
    """Close the package-local async driver (call on application shutdown)."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


def get_driver() -> AsyncDriver:
    if _driver is None:
        raise RuntimeError("Event-graph Neo4j driver is not initialized")
    return _driver


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async Neo4j session from the package driver."""
    driver = get_driver()
    async with driver.session() as session:
        yield session
