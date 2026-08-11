"""Async Neo4j driver lifecycle and FastAPI dependency (E2.1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession

from app.core.config import settings

_driver: AsyncDriver | None = None


async def init_neo4j_driver() -> None:
    """Create the shared async driver (call on application startup)."""
    global _driver
    if _driver is not None:
        return
    _driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )


async def close_neo4j_driver() -> None:
    """Close the shared async driver (call on application shutdown)."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


def get_driver() -> AsyncDriver:
    if _driver is None:
        raise RuntimeError("Neo4j driver is not initialized")
    return _driver


async def get_neo4j_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a Neo4j async session."""
    driver = get_driver()
    async with driver.session() as session:
        yield session


Neo4jSessionDep = Annotated[AsyncSession, Depends(get_neo4j_session)]
