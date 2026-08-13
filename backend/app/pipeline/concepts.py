"""Concept hashing and Neo4j MERGE helpers (autoschema compute_hash_id)."""

from __future__ import annotations

import hashlib

from neo4j import AsyncSession

MERGE_CONCEPT_LINK_CYPHER = """
MATCH (n:Node {id: $node_id})
MERGE (c:Concept {id: $concept_id})
ON CREATE SET c.name = $name
MERGE (n)-[:HAS_CONCEPT]->(c)
"""


def compute_hash_id(text: str) -> str:
    text = text + "_concept"
    hash_object = hashlib.sha256(text.encode("utf-8"))
    return hash_object.hexdigest()


async def merge_concept_and_link(session: AsyncSession, node_id: str, concept_name: str) -> None:
    concept_id = compute_hash_id(concept_name)
    await session.run(
        MERGE_CONCEPT_LINK_CYPHER,
        node_id=node_id,
        concept_id=concept_id,
        name=concept_name,
    )
