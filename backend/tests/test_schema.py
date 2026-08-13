"""Schema application and verification tests (Epic 1 + Node-query indexes)."""

from __future__ import annotations

import pytest

from app.db.schema import (
    REQUIRED_BTREE_INDEXES,
    REQUIRED_CONSTRAINTS,
    REQUIRED_FULLTEXT_INDEXES,
    REQUIRED_VECTOR_INDEXES,
    apply_schema_with_driver,
    fetch_constraint_names,
    fetch_index_info,
    load_schema_statements,
)
from tests.neo4j_gds import GDS_PINNED_VERSION, neo4j_gds_container, wait_for_gds

EXPECTED_SCHEMA_STATEMENTS = 17


@pytest.fixture(scope="module")
def neo4j_driver():
    """Fresh Neo4j Community container for schema tests (Docker required)."""
    container = neo4j_gds_container()
    container.start()
    driver = container.get_driver()
    try:
        wait_for_gds(driver)
        yield driver
    finally:
        driver.close()
        container.stop()


def test_schema_file_has_expected_statements():
    statements = load_schema_statements()
    joined = "\n".join(statements)
    assert len(statements) == EXPECTED_SCHEMA_STATEMENTS
    assert "CONSTRAINT fact_id" not in joined
    assert "CONSTRAINT query_log_id" not in joined
    assert "INDEX query_log_created_at" not in joined
    assert "INDEX fact_is_latest" not in joined
    assert "INDEX fact_type" not in joined
    assert "INDEX fact_doc" not in joined
    assert "INDEX fact_embedding" not in joined
    assert "chunk_id" in joined
    assert "node_query_log_id" in joined
    assert "node_query_log_created_at" in joined
    assert "chunk_embedding" in joined
    assert "node_id" in joined
    assert "concept_id" in joined
    assert "node_type" in joined
    assert "node_dreamed" in joined
    assert "node_merged_into" in joined
    assert "relation_is_latest" in joined
    assert "relation_normalized" in joined
    assert "node_embedding" in joined
    assert "concept_embedding" in joined
    assert "relation_embedding" in joined
    assert "node_concept_fulltext" in joined
    assert "relation_fulltext" in joined
    assert "768" in joined
    assert "cosine" in joined


def test_apply_schema_idempotent_and_indexes(neo4j_driver):
    # First application (clean Neo4j)
    assert apply_schema_with_driver(neo4j_driver) == EXPECTED_SCHEMA_STATEMENTS
    # Second application must not error (IF NOT EXISTS) — covers the
    # already-populated schema case (idempotency).
    assert apply_schema_with_driver(neo4j_driver) == EXPECTED_SCHEMA_STATEMENTS

    constraints = fetch_constraint_names(neo4j_driver)
    assert REQUIRED_CONSTRAINTS.issubset(constraints)

    indexes = fetch_index_info(neo4j_driver)
    by_name = {row["name"]: row for row in indexes}

    for name in REQUIRED_BTREE_INDEXES:
        assert name in by_name, f"missing btree index {name}"

    for name in REQUIRED_VECTOR_INDEXES:
        assert name in by_name, f"missing vector index {name}"
        assert str(by_name[name].get("type") or "").upper() == "VECTOR", name
        options = by_name[name].get("options") or {}
        config = options.get("indexConfig") or {}
        dims = config.get("vector.dimensions")
        sim = config.get("vector.similarity_function")
        assert dims == 768, f"{name} dimensions={dims}"
        assert str(sim).lower() == "cosine", f"{name} similarity={sim}"

    for name in REQUIRED_FULLTEXT_INDEXES:
        assert name in by_name, f"missing fulltext index {name}"
        assert str(by_name[name].get("type") or "").upper() == "FULLTEXT", name


def test_gds_version_is_pinned(neo4j_driver):
    version = wait_for_gds(neo4j_driver)
    assert version == GDS_PINNED_VERSION
