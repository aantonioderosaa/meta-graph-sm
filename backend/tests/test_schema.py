"""Schema application and verification tests (Epic 1 + Node-query indexes)."""

from __future__ import annotations

import pytest

from app.db.schema import (
    BACKBONE_REL_TYPES,
    FAMIGLIA_B_REL_TYPES,
    REQUIRED_BTREE_INDEXES,
    REQUIRED_CONSTRAINTS,
    REQUIRED_FULLTEXT_INDEXES,
    REQUIRED_VECTOR_INDEXES,
    SCHEMA_PATH,
    apply_schema_with_driver,
    fetch_constraint_names,
    fetch_index_info,
    load_schema_statements,
)
from app.models.kernel import IS_A, MEMBER_OF, SpecialRelationType
from tests.neo4j_gds import GDS_PINNED_VERSION, neo4j_gds_container, wait_for_gds

EXPECTED_SCHEMA_STATEMENTS = 27


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
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
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
    assert "node_summary_embedding" in joined
    assert "concept_embedding" in joined
    assert "relation_embedding" in joined
    assert "node_concept_fulltext" in joined
    assert "relation_fulltext" in joined
    assert "node_summary_fulltext" in joined
    assert "relation_witness_fulltext" in joined
    assert "768" in joined
    assert "cosine" in joined
    assert "concept_kernel_category" in joined
    assert "concept_parent_uri" in joined
    assert "node_kernel_category" in joined
    assert "identity_node_uri" in joined
    assert "connectivity_rule_triple" in joined
    assert "corpus_context_id" in joined
    assert "pending_hypothesis_id" in joined
    assert "PendingHypothesis" in raw
    assert "ContextLayerRun" in raw
    assert "AgentSearchRun" in raw
    for name in (
        "claim_target",
        "evidence_span",
        "witness_fragments",
        "evidence_gap",
        "confidence",
    ):
        assert name in raw
    for name in ("kernel_category", "parent_uri", "definition", "aliases", "promoted"):
        assert name in raw
    assert "canonical_summary" in raw
    for rel in FAMIGLIA_B_REL_TYPES:
        assert rel in raw
    for name in ("witnesses_a", "witnesses_b", "provenance", "valid_time", "system_time"):
        assert name in raw
    for rel in BACKBONE_REL_TYPES:
        assert rel in raw
    assert "HAS_CONCEPT" in raw
    for name in (
        "source_category",
        "relation_type",
        "target_category",
        "origin_fact_ids",
        "generalization_level",
    ):
        assert name in raw
    for name in ("summary_text", "updated_at", "document_count"):
        assert name in raw
    assert FAMIGLIA_B_REL_TYPES == {m.value.upper() for m in SpecialRelationType}
    assert BACKBONE_REL_TYPES == {IS_A.upper(), MEMBER_OF.upper()}


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
