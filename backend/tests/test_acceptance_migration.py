"""Fase 13 acceptance: DERIVED_FROM as Famiglia B, flag defaults, merge_nodes debt."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.db.schema import FAMIGLIA_B_REL_TYPES
from app.models.kernel import SpecialRelationType
from app.models.relations import RelationLabel
from app.pipeline.event_relation_resolution import LINK_SITUATION_CHUNK_CYPHER
from app.pipeline.ingestion import CREATE_NODE_CYPHER
from app.pipeline.node_resolution import merge_nodes, resolve_node
from app.pipeline.promote import is_skipped_relation


def test_derived_from_is_famiglia_b():
    assert SpecialRelationType.derived_from.value == "derived_from"
    assert "DERIVED_FROM" in FAMIGLIA_B_REL_TYPES
    assert SpecialRelationType.derived_from.value.upper() in FAMIGLIA_B_REL_TYPES


def test_ingestion_create_uses_derived_from_toward_chunk():
    compact = " ".join(CREATE_NODE_CYPHER.split())
    assert "CREATE (n)-[:DERIVED_FROM]->(c)" in compact
    assert "MATCH (c:Chunk {id: $chunk_id})" in compact
    situation = " ".join(LINK_SITUATION_CHUNK_CYPHER.split())
    assert "CREATE (n)-[:DERIVED_FROM]->(c)" in situation


def test_promote_skips_famiglia_b_including_derived_from():
    assert is_skipped_relation("DERIVED_FROM")
    assert is_skipped_relation("derived_from")
    assert is_skipped_relation("CONTRADICTS")
    assert is_skipped_relation("same_as")
    assert not is_skipped_relation("plays_for")


def test_metagraph_flag_defaults_locked():
    fields = Settings.model_fields
    assert fields["ENABLE_KERNEL_CLASSIFICATION"].default is True
    # Off by default: no clustering criterion yet to split a catch-all's members
    # into more than one sub-genre (app/pipeline/promote.py, is_promotable_parent).
    assert fields["ENABLE_PROMOTE"].default is False
    assert fields["ENABLE_TEMPORAL_TRANSITIONS"].default is True
    assert fields["ENABLE_JUDGE"].default is True
    assert fields["ENABLE_FACET_IDENTITY"].default is False
    assert fields["ENABLE_DERIVES"].default is False
    assert fields["ENABLE_CONTEXT_LAYER"].default is False
    assert fields["PENDING_HYPOTHESIS_LISTEN_WINDOW"].default == 5
    assert fields["CONTEXT_AGENT_MAX_TURNS"].default == 4
    assert fields["ENABLE_GENERIC_INSTANCES"].default is False
    assert fields["GENERIC_INSTANCE_MIN_OBSERVATIONS"].default == 2


def test_merge_nodes_still_present_and_called_when_facet_flag_off():
    source = Path(resolve_node.__code__.co_filename).read_text(encoding="utf-8")
    assert "async def merge_nodes" in source
    assert "await merge_nodes(session, node_id, canon_id)" in source
    assert "if settings.ENABLE_FACET_IDENTITY:" in source
    assert merge_nodes.__name__ == "merge_nodes"
    assert RelationLabel.replaces.value == "replaces"
    assert RelationLabel.extends.value == "extends"
