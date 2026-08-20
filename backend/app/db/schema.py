"""Neo4j schema helpers (tech-spec §4.2)."""

from __future__ import annotations

import os
from pathlib import Path

from neo4j import Driver, GraphDatabase

from app.models.kernel import IS_A, MEMBER_OF, SpecialRelationType

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.cypher"

REQUIRED_CONSTRAINTS = {
    "chunk_id",
    "node_query_log_id",
    "node_id",
    "concept_id",
    "identity_node_uri",
    "connectivity_rule_triple",
    "corpus_context_id",
}
REQUIRED_BTREE_INDEXES = {
    "chunk_doc",
    "node_query_log_created_at",
    "node_type",
    "node_dreamed",
    "node_merged_into",
    "relation_is_latest",
    "relation_normalized",
    "concept_kernel_category",
    "concept_parent_uri",
    "node_kernel_category",
}
REQUIRED_VECTOR_INDEXES = {
    "chunk_embedding",
    "node_embedding",
    "concept_embedding",
    "relation_embedding",
    "node_summary_embedding",
}
REQUIRED_FULLTEXT_INDEXES = {
    "node_concept_fulltext",
    "relation_fulltext",
    "node_summary_fulltext",
    "relation_witness_fulltext",
}

# Dedicated Neo4j relationship types (no CREATE TYPE). Uppercased kernel values so
# schema and kernel cannot drift. IS_A / MEMBER_OF must never share one rel type.
FAMIGLIA_B_REL_TYPES: frozenset[str] = frozenset(m.value.upper() for m in SpecialRelationType)
BACKBONE_REL_TYPES: frozenset[str] = frozenset({IS_A.upper(), MEMBER_OF.upper()})


def load_schema_statements(path: Path = SCHEMA_PATH) -> list[str]:
    """Split schema.cypher into individual executable statements."""
    raw = path.read_text(encoding="utf-8")
    statements: list[str] = []
    buffer: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buffer).rstrip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
            buffer = []
    if buffer:
        stmt = "\n".join(buffer).strip()
        if stmt:
            statements.append(stmt)
    return statements


def apply_schema_with_driver(driver: Driver) -> int:
    """Execute every schema statement with the given driver. Returns statement count."""
    statements = load_schema_statements()
    with driver.session() as session:
        for statement in statements:
            session.run(statement).consume()
    return len(statements)


def apply_schema(
    uri: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> int:
    """Open a driver, apply schema, close. Returns statement count."""
    uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = user or os.getenv("NEO4J_USER", "neo4j")
    password = password or os.getenv("NEO4J_PASSWORD", "changeme")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        return apply_schema_with_driver(driver)
    finally:
        driver.close()


def fetch_constraint_names(driver: Driver) -> set[str]:
    with driver.session() as session:
        records = session.run("SHOW CONSTRAINTS YIELD name RETURN name").data()
    return {row["name"] for row in records}


def fetch_index_info(driver: Driver) -> list[dict]:
    with driver.session() as session:
        return session.run(
            "SHOW INDEXES YIELD name, type, options RETURN name, type, options"
        ).data()
