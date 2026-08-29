"""Fase 14.2: Metagraph additive schema objects. Requires Docker / testcontainers."""

from __future__ import annotations

import pytest

from app.db.schema import (
    REQUIRED_CONSTRAINTS,
    REQUIRED_FULLTEXT_INDEXES,
    REQUIRED_VECTOR_INDEXES,
    apply_schema_with_driver,
    fetch_constraint_names,
    fetch_index_info,
)
from tests.neo4j_gds import neo4j_gds_container, wait_for_gds

METAGRAPH_CONSTRAINTS = {
    "connectivity_rule_triple",
    "corpus_context_id",
}
METAGRAPH_BTREE = {
    "concept_kernel_category",
    "concept_parent_uri",
    "node_kernel_category",
}
LEGACY_NODE_CONCEPT_CONSTRAINTS = {"node_id", "concept_id"}


@pytest.fixture(scope="module")
def neo4j_driver():
    """Fresh Neo4j Community container (Docker required)."""
    container = neo4j_gds_container()
    container.start()
    driver = container.get_driver()
    try:
        wait_for_gds(driver)
        yield driver
    finally:
        driver.close()
        container.stop()


def test_fase2_constraints_indexes_and_merge_labels(neo4j_driver):
    apply_schema_with_driver(neo4j_driver)

    constraints = fetch_constraint_names(neo4j_driver)
    assert METAGRAPH_CONSTRAINTS.issubset(constraints)
    assert LEGACY_NODE_CONCEPT_CONSTRAINTS.issubset(constraints)
    assert REQUIRED_CONSTRAINTS.issubset(constraints)

    indexes = fetch_index_info(neo4j_driver)
    by_name = {row["name"]: row for row in indexes}
    for name in METAGRAPH_BTREE:
        assert name in by_name, f"missing btree index {name}"

    vector_names = {
        name
        for name, row in by_name.items()
        if str(row.get("type") or "").upper() == "VECTOR"
    }
    assert vector_names == REQUIRED_VECTOR_INDEXES
    assert "node_summary_embedding" in vector_names

    fulltext_names = {
        name
        for name, row in by_name.items()
        if str(row.get("type") or "").upper() == "FULLTEXT"
    }
    assert REQUIRED_FULLTEXT_INDEXES.issubset(fulltext_names)
    assert "node_summary_fulltext" in fulltext_names
    assert "relation_witness_fulltext" in fulltext_names

    with neo4j_driver.session() as session:
        session.run(
            """
            MERGE (r:ConnectivityRule {
              source_category: 'Agente',
              relation_type: 'knows',
              target_category: 'Agente'
            })
            """
        ).consume()
        session.run("MERGE (c:CorpusContext {id: 'default'})").consume()
        session.run("MERGE (j:JudgeRun {id: 'job-schema-f14'})").consume()
        row = session.run(
            """
            MATCH (r:ConnectivityRule {source_category: 'Agente'})
            MATCH (c:CorpusContext {id: 'default'})
            MATCH (j:JudgeRun {id: 'job-schema-f14'})
            RETURN count(r) AS rules, count(c) AS contexts, count(j) AS runs
            """
        ).single()
    assert row is not None
    assert row["rules"] >= 1
    assert row["contexts"] >= 1
    assert row["runs"] >= 1
