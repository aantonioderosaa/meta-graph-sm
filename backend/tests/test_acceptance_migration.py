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
    assert fields["ENABLE_DERIVES"].default is False
    assert fields["ENABLE_EVENT_TRIAGE"].default is False
    assert fields["PENDING_HYPOTHESIS_LISTEN_WINDOW"].default == 5
    # EVENT_TRIAGE_MAX_TURNS removed: the per-event loop is a fixed three-phase
    # pipeline now (search, inspect, decide), not an open turn budget — see
    # EVENT_TRIAGE_MAX_SEARCH_QUERIES / EVENT_TRIAGE_MAX_INSPECT_NODES in
    # event_triage.py (module constants, not Settings, same as EVENT_TRIAGE_MAX_SLOT_FANOUT).
    assert "EVENT_TRIAGE_MAX_TURNS" not in fields
    # ENABLE_FACET_IDENTITY / ENABLE_CONTEXT_LAYER / CONTEXT_AGENT_MAX_TURNS
    # removed with identity_resolution.py / relevance_gate.py /
    # pending_hypothesis.py / context_agent.py / quantifier_events.py /
    # retraction.py — dead flags, no remaining code path to gate.
    assert "ENABLE_FACET_IDENTITY" not in fields
    assert "ENABLE_CONTEXT_LAYER" not in fields
    assert "CONTEXT_AGENT_MAX_TURNS" not in fields


def test_merge_nodes_always_called_identity_is_destructive_merge_only():
    """Facet identity (Fase 8) is gone: resolve_node has one dedup path now."""
    source = Path(resolve_node.__code__.co_filename).read_text(encoding="utf-8")
    assert "async def merge_nodes" in source
    assert "await merge_nodes(session, node_id, canon_id)" in source
    assert "ENABLE_FACET_IDENTITY" not in source
    assert merge_nodes.__name__ == "merge_nodes"
    assert RelationLabel.replaces.value == "replaces"
    assert RelationLabel.extends.value == "extends"
