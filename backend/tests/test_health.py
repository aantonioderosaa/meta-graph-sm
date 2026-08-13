"""Health endpoint integration tests (E2.1)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.neo4j_client import close_neo4j_driver, init_neo4j_driver
from app.db.schema import apply_schema_with_driver
from app.main import app
from tests.neo4j_gds import neo4j_gds_container


@pytest.fixture(scope="module")
def neo4j_container():
    container = neo4j_gds_container()
    container.start()
    yield container
    container.stop()


@pytest.fixture
async def health_client(neo4j_container, monkeypatch):
    monkeypatch.setattr(settings, "NEO4J_URI", neo4j_container.get_connection_url())
    monkeypatch.setattr(settings, "NEO4J_USER", neo4j_container.username)
    monkeypatch.setattr(settings, "NEO4J_PASSWORD", neo4j_container.password)
    monkeypatch.setattr(settings, "AUTO_MIGRATE", False)

    await close_neo4j_driver()
    await init_neo4j_driver()
    driver = neo4j_container.get_driver()
    apply_schema_with_driver(driver)

    # GDS plugin can lag behind Bolt readiness — wait until callable.
    import time

    from neo4j.exceptions import ClientError

    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            with driver.session() as session:
                session.run("CALL gds.version() YIELD gdsVersion RETURN gdsVersion").consume()
            break
        except ClientError:
            time.sleep(2)
    else:
        raise RuntimeError("GDS did not become available in time")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await close_neo4j_driver()


@pytest.mark.asyncio
async def test_health_ok_when_neo4j_and_gds_up(health_client: AsyncClient):
    response = await health_client.get("/health")
    assert response.status_code == 200, response.text
    assert response.json() == {"neo4j": "ok", "gds": "ok"}


@pytest.mark.asyncio
async def test_health_503_when_neo4j_unreachable(monkeypatch):
    monkeypatch.setattr(settings, "NEO4J_URI", "bolt://127.0.0.1:17999")
    monkeypatch.setattr(settings, "NEO4J_USER", "neo4j")
    monkeypatch.setattr(settings, "NEO4J_PASSWORD", "changeme")
    monkeypatch.setattr(settings, "AUTO_MIGRATE", False)

    await close_neo4j_driver()
    await init_neo4j_driver()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    await close_neo4j_driver()

    assert response.status_code == 503
    assert "detail" in response.json()
