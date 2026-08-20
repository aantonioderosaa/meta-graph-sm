"""Fase 21.1 / 21.4: quantifier → Evento, never a collective entity. FakeSession."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import event_bus
from app.models.kernel import EntityKernelType, RelationKernelType
from app.models.node_extraction import (
    EntityExtractionResult,
    EventEntityExtractionResult,
    EventRelationExtractionResult,
    PairRelationDecision,
)
from app.pipeline.chunking import Chunk
from app.pipeline.connectivity_rules import (
    MERGE_CONNECTIVITY_RULE_CYPHER,
    READ_CONCEPT_ANCESTORS_CYPHER,
    READ_NODE_TYPE_TOKEN_CYPHER,
)
from app.pipeline.entity_relation_resolution import APPLY_DIFFERENT_TAIL_SUPERSEDES_CYPHER
from app.pipeline.event_relation_resolution import (
    CREATE_SITUATION_EVENT_CYPHER,
    FIND_EVENT_BY_NAME_CYPHER,
    LINK_SITUATION_CHUNK_CYPHER,
    MERGE_SITUATION_PARTICIPATES_CYPHER,
    SITUATION_NORMALIZED_RELATION,
)
from app.pipeline.ingestion import CREATE_NODE_RELATION_CYPHER, process_chunk_node_extraction
from app.pipeline.pending_hypothesis import (
    MERGE_HYPOTHESIS_CYPHER,
    READ_HYPOTHESIS_CYPHER,
    RESOLVE_HYPOTHESIS_CYPHER,
)
from app.pipeline.quantifier_events import (
    CLOSED_SCOPE_MIN_MEMBERS,
    FIND_LATEST_STATE_REL_CYPHER,
    FIND_NAMED_GENRE_MEMBERS_CYPHER,
    FIND_PLACE_SCOPED_MEMBERS_CYPHER,
    FIND_SCOPED_DOC_MEMBERS_CYPHER,
    maybe_resolve_quantifier_scope,
    resolve_quantifier_scope,
)

JOB_ID = "job-f21-q"
DOC_SCENE = "doc-kitchen"
DOC_OTHER = "doc-elsewhere"
CANE = "concept-cane"
CUCINA = "place-cucina"
FIDO, REX, BOBI, SPOT = "fido", "rex", "bobi", "spot"
COLLECTIVE_NAMES = frozenset({"cani", "i cani", "cane", "dogs", "the dogs", "dog"})


class FakeResult:
    def __init__(self, records: list[dict] | None = None):
        self._records = records or []

    async def single(self):
        return self._records[0] if self._records else None

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for record in self._records:
            yield record


class SceneGraph:
    def __init__(self) -> None:
        self.concepts: dict[str, dict] = {}
        self.nodes: dict[str, dict] = {}
        self.chunks: dict[str, dict] = {}
        self.member_of: dict[str, str] = {}
        self.isa: dict[str, str] = {}
        self.derived_from: list[tuple[str, str]] = []
        self.relations: list[dict] = []
        self.famiglia: list[dict] = []
        self.hypotheses: dict[str, dict] = {}
        self.rel_seq = 0

    def add_concept(self, concept_id: str, name: str) -> None:
        self.concepts[concept_id] = {"id": concept_id, "name": name}

    def add_chunk(self, chunk_id: str, doc_id: str) -> None:
        self.chunks[chunk_id] = {"id": chunk_id, "doc_id": doc_id}

    def add_node(
        self,
        node_id: str,
        name: str,
        *,
        node_type: str = "entity",
        kernel_category: str | None = None,
        doc_id: str | None = None,
        chunk_id: str | None = None,
    ) -> None:
        self.nodes[node_id] = {
            "id": node_id,
            "name": name,
            "type": node_type,
            "merged_into": None,
            "kernel_category": kernel_category,
        }
        if chunk_id:
            self.derived_from.append((node_id, chunk_id))
        elif doc_id:
            cid = f"chunk-{doc_id}-{node_id}"
            if cid not in self.chunks:
                self.add_chunk(cid, doc_id)
            self.derived_from.append((node_id, cid))

    def add_relation(
        self,
        from_id: str,
        to_id: str,
        relation: str,
        *,
        kernel_parent: str | None = None,
        normalized_relation: str | None = None,
        is_latest: bool = True,
    ) -> dict:
        self.rel_seq += 1
        row = {
            "id": f"rel-{self.rel_seq}",
            "from_id": from_id,
            "to_id": to_id,
            "relation": relation,
            "normalized_relation": normalized_relation or relation,
            "kernel_parent": kernel_parent,
            "is_latest": is_latest,
            "created_at": f"2026-01-01T00:00:{self.rel_seq:02d}",
        }
        self.relations.append(row)
        return row

    def home_matches_genre(self, home_id: str, genre_names: list[str]) -> bool:
        wanted = {n.casefold() for n in genre_names}
        current = home_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            concept = self.concepts.get(current)
            if concept and str(concept.get("name") or "").casefold() in wanted:
                return True
            current = self.isa.get(current)
        return False

    def doc_ids_of(self, node_id: str) -> set[str]:
        docs: set[str] = set()
        for nid, cid in self.derived_from:
            if nid != node_id:
                continue
            chunk = self.chunks.get(cid)
            if chunk:
                docs.add(str(chunk["doc_id"]))
        return docs


class SceneSession:
    def __init__(self, graph: SceneGraph | None = None) -> None:
        self.graph = graph or SceneGraph()
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        g = self.graph
        if cypher == FIND_SCOPED_DOC_MEMBERS_CYPHER:
            doc_id = kwargs["doc_id"]
            names = list(kwargs["genre_names"])
            rows = []
            for nid, node in g.nodes.items():
                if node.get("merged_into") or node.get("type") == "event":
                    continue
                home = g.member_of.get(nid)
                if not home or not g.home_matches_genre(home, names):
                    continue
                if doc_id not in g.doc_ids_of(nid):
                    continue
                rows.append({"id": nid, "name": node["name"]})
            return FakeResult(rows)
        if cypher == FIND_NAMED_GENRE_MEMBERS_CYPHER:
            witnesses = {str(w).casefold() for w in kwargs.get("witness_names") or []}
            names = list(kwargs["genre_names"])
            rows = []
            for nid, node in g.nodes.items():
                if node.get("merged_into") or node.get("type") == "event":
                    continue
                if str(node.get("name") or "").casefold() not in witnesses:
                    continue
                home = g.member_of.get(nid)
                if not home or not g.home_matches_genre(home, names):
                    continue
                rows.append({"id": nid, "name": node["name"]})
            return FakeResult(rows)
        if cypher == FIND_PLACE_SCOPED_MEMBERS_CYPHER:
            doc_id = kwargs["doc_id"]
            names = list(kwargs["genre_names"])
            spatial = kwargs.get("spatial_parent") or RelationKernelType.Spaziale.value
            place_ids = {
                nid
                for nid, node in g.nodes.items()
                if (
                    node.get("kernel_category") == EntityKernelType.Luogo.value
                    or node.get("type") == "place"
                )
                and doc_id in g.doc_ids_of(nid)
                and not node.get("merged_into")
            }
            rows = []
            for rel in g.relations:
                if not rel.get("is_latest", True):
                    continue
                if rel.get("kernel_parent") != spatial:
                    continue
                if rel["to_id"] not in place_ids:
                    continue
                leaf = g.nodes.get(rel["from_id"])
                if leaf is None or leaf.get("merged_into") or leaf.get("type") == "event":
                    continue
                home = g.member_of.get(rel["from_id"])
                if not home or not g.home_matches_genre(home, names):
                    continue
                rows.append({"id": rel["from_id"], "name": leaf["name"]})
            return FakeResult(rows)
        if cypher == FIND_LATEST_STATE_REL_CYPHER:
            member_id = kwargs["member_id"]
            spatial = kwargs.get("spatial_parent")
            candidates = [
                rel
                for rel in g.relations
                if rel["from_id"] == member_id
                and rel.get("is_latest", True)
                and rel.get("normalized_relation") != "participates"
                and (
                    rel.get("kernel_parent") == spatial or rel.get("kernel_parent") == "Stato"
                )
            ]
            candidates.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
            if not candidates:
                return FakeResult([])
            rel = candidates[0]
            other = g.nodes.get(rel["to_id"], {})
            return FakeResult(
                [
                    {
                        "tail_id": rel["to_id"],
                        "tail_name": other.get("name"),
                        "relation": rel["relation"],
                        "normalized_relation": rel.get("normalized_relation"),
                        "kernel_parent": rel.get("kernel_parent"),
                        "created_at": rel.get("created_at"),
                    }
                ]
            )
        if cypher == FIND_EVENT_BY_NAME_CYPHER:
            name = kwargs["name"]
            for node in g.nodes.values():
                if (
                    node.get("type") == "event"
                    and node.get("name") == name
                    and node.get("merged_into") is None
                ):
                    return FakeResult([{"id": node["id"]}])
            return FakeResult([])
        if cypher == CREATE_SITUATION_EVENT_CYPHER:
            nid = kwargs["id"]
            g.nodes[nid] = {
                "id": nid,
                "name": kwargs["name"],
                "type": "event",
                "merged_into": None,
                "kernel_category": kwargs["kernel_category"],
            }
            return FakeResult([{"id": nid}])
        if cypher == LINK_SITUATION_CHUNK_CYPHER:
            if kwargs["node_id"] in g.nodes:
                cid = kwargs["chunk_id"]
                if cid not in g.chunks:
                    g.add_chunk(cid, DOC_SCENE)
                g.derived_from.append((kwargs["node_id"], cid))
            return FakeResult([])
        if cypher == MERGE_SITUATION_PARTICIPATES_CYPHER:
            event_id = kwargs["event_id"]
            participant_id = kwargs["participant_id"]
            if event_id not in g.nodes or participant_id not in g.nodes:
                return FakeResult([])
            for rel in g.relations:
                if (
                    rel["from_id"] == event_id
                    and rel["to_id"] == participant_id
                    and rel.get("normalized_relation") == kwargs["normalized_relation"]
                ):
                    return FakeResult([])
            g.add_relation(
                event_id,
                participant_id,
                kwargs["relation"],
                kernel_parent=kwargs["kernel_parent"],
                normalized_relation=kwargs["normalized_relation"],
            )
            return FakeResult([])
        if cypher == CREATE_NODE_RELATION_CYPHER:
            g.add_relation(
                kwargs["head_id"],
                kwargs["tail_id"],
                kwargs["relation"],
                kernel_parent=kwargs.get("kernel_parent"),
                normalized_relation=kwargs.get("normalized_relation"),
            )
            return FakeResult([])
        if cypher == APPLY_DIFFERENT_TAIL_SUPERSEDES_CYPHER:
            head = kwargs["head_id"]
            old_tail = kwargs["old_tail_id"]
            kernel = kwargs.get("kernel_parent") or ""
            old_relation = kwargs.get("old_relation") or ""
            for rel in g.relations:
                if (
                    rel["from_id"] == head
                    and rel["to_id"] == old_tail
                    and rel.get("is_latest", True)
                    and (rel.get("kernel_parent") or "") == kernel
                    and (rel.get("relation") or "") == old_relation
                ):
                    rel["is_latest"] = False
            g.famiglia.append(
                {
                    "src": kwargs["new_tail_id"],
                    "dst": old_tail,
                    "rel_type": "SUPERSEDES",
                    "subject_id": head,
                }
            )
            return FakeResult([])
        if cypher == READ_HYPOTHESIS_CYPHER:
            hyp = g.hypotheses.get(kwargs["id"])
            return FakeResult([dict(hyp)] if hyp else [])
        if cypher == MERGE_HYPOTHESIS_CYPHER:
            hid = kwargs["id"]
            existing = g.hypotheses.get(hid, {})
            g.hypotheses[hid] = {**existing, **kwargs}
            return FakeResult([])
        if cypher == RESOLVE_HYPOTHESIS_CYPHER:
            hyp = g.hypotheses.get(kwargs["id"])
            if hyp is None:
                return FakeResult([])
            hyp["status"] = kwargs["status"]
            return FakeResult([dict(hyp)])
        if cypher in {
            READ_NODE_TYPE_TOKEN_CYPHER,
            READ_CONCEPT_ANCESTORS_CYPHER,
            MERGE_CONNECTIVITY_RULE_CYPHER,
        }:
            if cypher == READ_NODE_TYPE_TOKEN_CYPHER:
                node = g.nodes.get(kwargs["node_id"], {})
                return FakeResult(
                    [
                        {
                            "kernel_category": node.get("kernel_category"),
                            "concept_id": g.member_of.get(kwargs["node_id"]),
                            "concept_name": None,
                        }
                    ]
                )
            return FakeResult([])
        return FakeResult([])


@pytest.fixture(autouse=True)
def _stub_embed(monkeypatch):
    monkeypatch.setattr("app.pipeline.embeddings.embed", lambda _text: [0.0] * 768)
    event_bus.reset_event_bus()
    yield
    event_bus.reset_event_bus()


def _three_dogs_scene() -> SceneGraph:
    graph = SceneGraph()
    graph.add_concept(CANE, "cane")
    graph.add_chunk("chunk-kitchen", DOC_SCENE)
    graph.add_chunk("chunk-other", DOC_OTHER)
    for nid, name in ((FIDO, "Fido"), (REX, "Rex"), (BOBI, "Bobi")):
        graph.add_node(nid, name, kernel_category=EntityKernelType.Agente.value, doc_id=DOC_SCENE)
        graph.member_of[nid] = CANE
    graph.add_node(
        SPOT, "Spot", kernel_category=EntityKernelType.Agente.value, doc_id=DOC_OTHER
    )
    graph.member_of[SPOT] = CANE
    graph.add_node(
        CUCINA,
        "cucina",
        kernel_category=EntityKernelType.Luogo.value,
        doc_id=DOC_SCENE,
    )
    for nid in (FIDO, REX, BOBI):
        graph.add_relation(
            nid,
            CUCINA,
            "è in",
            kernel_parent=RelationKernelType.Spaziale.value,
            normalized_relation="è in",
        )
    return graph


def _closed_chunk() -> Chunk:
    return Chunk(
        id="chunk-quantifier",
        doc_id=DOC_SCENE,
        text="Tutti i cani sono usciti.",
    )


@pytest.mark.asyncio
async def test_three_dogs_in_scope_become_one_evento_not_a_collective():
    graph = _three_dogs_scene()
    session = SceneSession(graph)
    result = await resolve_quantifier_scope(session, _closed_chunk(), concept_hint="cane")

    assert result.closed is True
    assert result.event_id
    assert set(result.member_ids) == {FIDO, REX, BOBI}
    assert SPOT not in result.member_ids

    event = graph.nodes[result.event_id]
    assert event["type"] == "event"
    assert event["kernel_category"] == EntityKernelType.Evento.value
    assert event["name"].casefold() not in COLLECTIVE_NAMES

    collective = [
        n
        for n in graph.nodes.values()
        if n.get("type") != "event" and str(n.get("name") or "").casefold() in COLLECTIVE_NAMES
    ]
    assert collective == []

    participates = [
        rel
        for rel in graph.relations
        if rel.get("normalized_relation") == SITUATION_NORMALIZED_RELATION
    ]
    assert len(participates) == 3
    assert {rel["to_id"] for rel in participates} == {FIDO, REX, BOBI}
    assert all(rel["from_id"] == result.event_id for rel in participates)
    assert all(
        rel["kernel_parent"] == RelationKernelType.Partecipativa.value for rel in participates
    )

    supersedes = [edge for edge in graph.famiglia if edge["rel_type"] == "SUPERSEDES"]
    assert len(supersedes) == 3
    assert {edge["dst"] for edge in supersedes} == {CUCINA}
    for rel in graph.relations:
        if rel["to_id"] == CUCINA and rel["from_id"] in {FIDO, REX, BOBI}:
            assert rel["is_latest"] is False
    assert not any("DELETE" in cy for cy, _ in session.calls)


@pytest.mark.asyncio
async def test_quantifier_without_scene_opens_hypothesis_zero_s0():
    graph = SceneGraph()
    graph.add_concept(CANE, "cane")
    session = SceneSession(graph)
    chunk = Chunk(id="c-bare", doc_id="doc-orphan", text="Tutti i cani sono usciti.")
    result = await resolve_quantifier_scope(session, chunk, concept_hint="cane")

    assert result.closed is False
    assert result.event_id is None
    assert result.member_ids == ()
    assert result.hypothesis_id
    hyp = graph.hypotheses[result.hypothesis_id]
    assert hyp["status"] == "open"
    assert hyp["confidence"] in {"low", "medium", "high"}
    assert "scope" in str(hyp["evidence_gap"]).casefold()
    assert not any(n.get("type") == "event" for n in graph.nodes.values())
    assert graph.relations == []
    assert not any(cy == CREATE_SITUATION_EVENT_CYPHER for cy, _ in session.calls)
    assert not any(cy == CREATE_NODE_RELATION_CYPHER for cy, _ in session.calls)
    assert any(cy == MERGE_HYPOTHESIS_CYPHER for cy, _ in session.calls)


@pytest.mark.asyncio
async def test_maybe_quantifier_flag_off_is_noop(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", False)
    monkeypatch.setattr("app.pipeline.quantifier_events.settings.ENABLE_CONTEXT_LAYER", False)
    session = SceneSession(_three_dogs_scene())
    out = await maybe_resolve_quantifier_scope(session, _closed_chunk(), concept_hint="cane")
    assert out is None
    assert session.calls == []


def test_closed_scope_minimum_is_two_documented_members():
    assert CLOSED_SCOPE_MIN_MEMBERS == 2


def test_quantifier_module_never_creates_collective_or_deletes():
    source = Path(resolve_quantifier_scope.__code__.co_filename).read_text(encoding="utf-8")
    assert "DETACH DELETE" not in source
    assert "write_node(" not in source
    assert "CREATE_NODE_CYPHER" not in source
    assert "reify_shared_situation" in source
    assert "CLOSED_SCOPE_MIN_MEMBERS" in source


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_ingest_flag_off_does_not_run_quantifier_hook(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", False)
    monkeypatch.setattr("app.pipeline.ingestion.settings.ENABLE_CONTEXT_LAYER", False)
    monkeypatch.setattr("app.pipeline.quantifier_events.settings.ENABLE_CONTEXT_LAYER", False)

    async def mock_entities(_text, **_kwargs):
        return EntityExtractionResult(entities=[])

    async def mock_pair(*_args, **_kwargs):
        return PairRelationDecision(related=False)

    async def mock_empty(*_args, **_kwargs):
        return EventEntityExtractionResult(participations=[])

    async def mock_rels(*_args, **_kwargs):
        return EventRelationExtractionResult(triples=[])

    monkeypatch.setattr("app.pipeline.node_extraction.extract_entities", mock_entities)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_pair_relation", mock_pair)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_entities", mock_empty)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_relations", mock_rels)

    session = SceneSession()
    await process_chunk_node_extraction(session, _closed_chunk(), DOC_SCENE, JOB_ID)
    f21 = {
        FIND_SCOPED_DOC_MEMBERS_CYPHER,
        FIND_NAMED_GENRE_MEMBERS_CYPHER,
        FIND_PLACE_SCOPED_MEMBERS_CYPHER,
        CREATE_SITUATION_EVENT_CYPHER,
        MERGE_HYPOTHESIS_CYPHER,
    }
    assert not any(cy in f21 for cy, _ in session.calls)
