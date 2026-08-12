"""Schema application and verification tests (Epic 1)."""

from __future__ import annotations

import pytest
from testcontainers.community.neo4j import Neo4jContainer

from app.db.schema import (
    REQUIRED_BTREE_INDEXES,
    REQUIRED_CONSTRAINTS,
    REQUIRED_VECTOR_INDEXES,
    apply_schema_with_driver,
    fetch_constraint_names,
    fetch_index_info,
    load_schema_statements,
)

NEO4J_IMAGE = "neo4j:5.24-community"


@pytest.fixture(scope="module")
def neo4j_driver():
    """Fresh Neo4j Community container for schema tests (Docker required)."""
    container = (
        Neo4jContainer(NEO4J_IMAGE)
        .with_env("NEO4J_PLUGINS", '["graph-data-science"]')
        .with_env("NEO4J_dbms_security_procedures_unrestricted", "gds.*")
    )
    container.start()
    driver = container.get_driver()
    try:
        yield driver
    finally:
        driver.close()
        container.stop()


def test_schema_file_has_expected_statements():
    statements = load_schema_statements()
    joined = "\n".join(statements)
    assert len(statements) == 10
    assert "fact_id" in joined
    assert "chunk_id" in joined
    assert "query_log_id" in joined
    assert "query_log_created_at" in joined
    assert "fact_embedding" in joined
    assert "chunk_embedding" in joined
    assert "768" in joined
    assert "cosine" in joined


def test_apply_schema_idempotent_and_indexes(neo4j_driver):
    # First application
    assert apply_schema_with_driver(neo4j_driver) == 10
    # Second application must not error (IF NOT EXISTS)
    assert apply_schema_with_driver(neo4j_driver) == 10

    constraints = fetch_constraint_names(neo4j_driver)
    assert REQUIRED_CONSTRAINTS.issubset(constraints)

    indexes = fetch_index_info(neo4j_driver)
    by_name = {row["name"]: row for row in indexes}

    for name in REQUIRED_BTREE_INDEXES:
        assert name in by_name, f"missing btree index {name}"

    for name in REQUIRED_VECTOR_INDEXES:
        assert name in by_name, f"missing vector index {name}"
        options = by_name[name].get("options") or {}
        config = options.get("indexConfig") or {}
        dims = config.get("vector.dimensions")
        sim = config.get("vector.similarity_function")
        assert dims == 768, f"{name} dimensions={dims}"
        assert str(sim).lower() == "cosine", f"{name} similarity={sim}"
