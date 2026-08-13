"""Pinned GDS plugin helper for Testcontainers Neo4j."""

from __future__ import annotations

import time
from pathlib import Path

from neo4j import Driver
from neo4j.exceptions import ClientError, ServiceUnavailable
from testcontainers.community.neo4j import Neo4jContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

NEO4J_IMAGE = "neo4j:5.24-community"
GDS_PINNED_VERSION = "2.12.0"
GDS_PLUGINS_DIR = Path(__file__).resolve().parents[2] / "neo4j-plugins"
GDS_JAR_NAME = f"neo4j-graph-data-science-{GDS_PINNED_VERSION}.jar"


def gds_jar_path() -> Path:
    return GDS_PLUGINS_DIR / GDS_JAR_NAME


def neo4j_gds_container(*, startup_timeout: int = 180) -> Neo4jContainer:
    """Neo4j with the committed GDS 2.12.0 jar bind-mounted — no plugin download."""
    jar = gds_jar_path()
    if not jar.is_file():
        raise FileNotFoundError(f"Pinned GDS jar missing: {jar}")
    return (
        Neo4jContainer(NEO4J_IMAGE)
        .with_volume_mapping(str(GDS_PLUGINS_DIR.resolve()), "/plugins", "rw")
        .with_env("NEO4J_dbms_security_procedures_unrestricted", "gds.*")
        .waiting_for(
            LogMessageWaitStrategy("Remote interface available at").with_startup_timeout(
                startup_timeout
            )
        )
    )


def wait_for_gds(driver: Driver, timeout: float = 90) -> str:
    """Block until gds.version() is callable; return the version string."""
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with driver.session() as session:
                record = session.run(
                    "CALL gds.version() YIELD gdsVersion RETURN gdsVersion"
                ).single()
            if record is not None:
                return str(record["gdsVersion"])
        except (ClientError, ServiceUnavailable) as exc:
            last = exc
            time.sleep(2)
    raise RuntimeError(f"GDS did not become available in time: {last}")
