"""Fase 23 acceptance: generic subdomain instances via the seventh judge task.

FakeSession / JudgeGraph only — no Docker, no OpenAI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.kernel import EntityKernelType
from app.pipeline.concepts import compute_hash_id
from app.pipeline.generic_instances import (
    GENERIC_INSTANCE_SUMMARY,
    ensure_generic_instance,
    generic_instance_id,
    redirect_to_generic,
)
from app.pipeline.judge import SpecificityVerdict, run_judge
from app.pipeline.node_extraction_prompts import (
    ENTITY_LIST_USER_PROMPT_TEMPLATE,
    build_entity_list_prompt,
)
from app.pipeline.node_resolution import PROMOTE_NEWER_SUMMARY_CYPHER
from tests.test_acceptance_judge import JudgeGraph

JOB_ID = "job-generic-f23"
PARENT = "parent-luogo"
CHILD = "child-citta"
STRADA = "n-strada"
VIANDANTE = "n-viandante"
FACT_TEXT = "Il viandante camminava lungo la strada all'inizio della storia."


def _background_graph() -> JudgeGraph:
    graph = JudgeGraph()
    graph.add_concept(PARENT, promoted=True, name="luogo", kernel_category="Luogo")
    graph.add_concept(
        CHILD,
        promoted=True,
        name="città",
        definition="insediamento urbano nominato",
        kernel_category="Luogo",
    )
    graph.set_isa(CHILD, PARENT)
    graph.add_node(
        STRADA,
        name="la strada",
        summary="una strada all'inizio della storia",
        kernel_category="Luogo",
    )
    graph.add_node(VIANDANTE, name="il viandante", kernel_category="Agente")
    graph.set_member_of(STRADA, PARENT)
    graph.add_relation(
        VIANDANTE,
        STRADA,
        relation=FACT_TEXT,
        kernel_parent="Spaziale",
        witnesses_a=["il viandante"],
        witnesses_b=["la strada"],
    )
    return graph


def _enable_generic(monkeypatch, min_obs: int = 2) -> None:
    monkeypatch.setattr("app.core.config.settings.ENABLE_JUDGE", True)
    monkeypatch.setattr("app.core.config.settings.ENABLE_GENERIC_INSTANCES", True)
    monkeypatch.setattr(
        "app.core.config.settings.GENERIC_INSTANCE_MIN_OBSERVATIONS", min_obs
    )


async def _always_specifico(*_args, **_kwargs) -> SpecificityVerdict:
    return SpecificityVerdict(decision="specifico")


async def _always_generico(*_args, **_kwargs) -> SpecificityVerdict:
    return SpecificityVerdict(decision="generico")


@pytest.mark.asyncio
async def test_specific_candidate_left_alone_counter_reset(monkeypatch):
    _enable_generic(monkeypatch)
    monkeypatch.setattr("app.pipeline.judge.classify_node_specificity", _always_specifico)
    graph = _background_graph()
    graph.nodes[STRADA]["generic_observation_count"] = 4

    stats = await run_judge(graph, JOB_ID, promoted_parent_ids=[PARENT])

    assert stats.generic_instances == 0
    assert graph.nodes[STRADA].get("merged_into") is None
    assert graph.nodes[STRADA]["generic_observation_count"] == 0
    assert graph.member_of[STRADA]["concept_id"] == PARENT
    assert STRADA in graph.nodes
    assert all(cy != PROMOTE_NEWER_SUMMARY_CYPHER for cy, _kw in graph.calls)


@pytest.mark.asyncio
async def test_generic_after_n_observations_redirects_without_overwriting_summary(
    monkeypatch,
):
    _enable_generic(monkeypatch, min_obs=2)
    monkeypatch.setattr("app.pipeline.judge.classify_node_specificity", _always_generico)
    graph = _background_graph()
    generic_id = await ensure_generic_instance(graph, PARENT, EntityKernelType.Luogo)
    graph.nodes[generic_id]["summary"] = "NON TOCCARE"

    first = await run_judge(graph, JOB_ID, promoted_parent_ids=[PARENT])
    assert first.generic_instances == 0
    assert graph.nodes[STRADA].get("merged_into") is None
    assert graph.nodes[STRADA]["generic_observation_count"] == 1

    second = await run_judge(graph, JOB_ID, promoted_parent_ids=[PARENT])
    assert second.generic_instances == 1
    assert STRADA in graph.nodes
    assert graph.nodes[STRADA]["merged_into"] == generic_id
    assert STRADA not in graph.member_of
    assert graph.nodes[generic_id]["summary"] == "NON TOCCARE"
    assert graph.member_of[generic_id]["concept_id"] == PARENT
    assert any(
        rel["src"] == VIANDANTE
        and rel["dst"] == generic_id
        and FACT_TEXT in str(rel.get("relation"))
        for rel in graph.relations
    )
    assert not any(rel["src"] == STRADA or rel["dst"] == STRADA for rel in graph.relations)
    assert all(cy != PROMOTE_NEWER_SUMMARY_CYPHER for cy, _kw in graph.calls)


@pytest.mark.asyncio
async def test_flag_off_zero_effect_even_if_judge_on(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_JUDGE", True)
    monkeypatch.setattr("app.core.config.settings.ENABLE_GENERIC_INSTANCES", False)
    llm_calls: list[object] = []

    async def boom_specificity(*_args, **_kwargs):
        llm_calls.append("specificity")
        return SpecificityVerdict(decision="generico")

    async def boom_structured(*_args, **_kwargs):
        llm_calls.append("structured")
        raise AssertionError("specificity LLM must not run when flag is off")

    monkeypatch.setattr("app.pipeline.judge.classify_node_specificity", boom_specificity)
    monkeypatch.setattr("app.pipeline.judge.call_structured", boom_structured)
    graph = _background_graph()
    graph.nodes[STRADA]["generic_observation_count"] = 9

    stats = await run_judge(graph, JOB_ID, promoted_parent_ids=[PARENT])

    assert stats.generic_instances == 0
    assert llm_calls == []
    assert graph.nodes[STRADA].get("merged_into") is None
    assert graph.member_of[STRADA]["concept_id"] == PARENT
    assert graph.nodes[STRADA]["generic_observation_count"] == 9
    assert not any(node.get("is_generic") for node in graph.nodes.values())


def test_entity_list_prompt_extracts_background_fact_witnesses():
    """F23.6: Passo A also extracts background elements that witness a fact."""
    assert "elementi di sfondo" in ENTITY_LIST_USER_PROMPT_TEMPLATE
    assert "la strada" in ENTITY_LIST_USER_PROMPT_TEMPLATE
    assert "camminava lungo la strada" in ENTITY_LIST_USER_PROMPT_TEMPLATE
    assert "estrai SOLO le entità importanti" not in ENTITY_LIST_USER_PROMPT_TEMPLATE
    _system, user = build_entity_list_prompt(
        "Il viandante camminava lungo la strada.", "corpus"
    )
    assert "elementi di sfondo" in user
    assert "la strada" in user


@pytest.mark.asyncio
async def test_first_generic_observation_increments_only(monkeypatch):
    _enable_generic(monkeypatch, min_obs=2)
    monkeypatch.setattr("app.pipeline.judge.classify_node_specificity", _always_generico)
    graph = _background_graph()

    stats = await run_judge(graph, JOB_ID, promoted_parent_ids=[PARENT])

    assert stats.generic_instances == 0
    assert graph.nodes[STRADA].get("merged_into") is None
    assert graph.nodes[STRADA]["generic_observation_count"] == 1
    assert graph.member_of[STRADA]["concept_id"] == PARENT
    assert any(rel["src"] == VIANDANTE and rel["dst"] == STRADA for rel in graph.relations)


@pytest.mark.asyncio
async def test_ensure_generic_instance_idempotent():
    graph = JudgeGraph()
    graph.add_concept(PARENT, promoted=True, kernel_category="Luogo")
    first = await ensure_generic_instance(graph, PARENT, EntityKernelType.Luogo)
    expected = compute_hash_id(f"generic:{PARENT}:Luogo")
    assert first == expected
    assert first == generic_instance_id(PARENT, "Luogo")
    assert graph.nodes[first]["is_generic"] is True
    assert graph.nodes[first]["type"] == "entity"
    assert graph.nodes[first]["summary"] == GENERIC_INSTANCE_SUMMARY
    graph.nodes[first]["summary"] = "NON TOCCARE"
    second = await ensure_generic_instance(graph, PARENT, "Luogo")
    assert second == first
    assert graph.nodes[first]["summary"] == "NON TOCCARE"
    assert graph.member_of[first]["concept_id"] == PARENT


@pytest.mark.asyncio
async def test_reraffine_match_is_not_classified_as_generic(monkeypatch):
    _enable_generic(monkeypatch, min_obs=1)
    classified: list[str] = []

    async def spy(job_id, name, summary, kernel_category, children):
        classified.append(name)
        return SpecificityVerdict(decision="generico")

    monkeypatch.setattr("app.pipeline.judge.classify_node_specificity", spy)
    graph = JudgeGraph()
    graph.add_concept(PARENT, promoted=True, name="luogo", kernel_category="Luogo")
    graph.add_concept(
        CHILD, promoted=True, name="città", definition="città", kernel_category="Luogo"
    )
    graph.set_isa(CHILD, PARENT)
    graph.add_node("n-match", name="città", summary="città", kernel_category="Luogo")
    graph.add_node(
        STRADA, name="la strada", summary="una strada", kernel_category="Luogo"
    )
    graph.set_member_of("n-match", PARENT)
    graph.set_member_of(STRADA, PARENT)

    stats = await run_judge(graph, JOB_ID, promoted_parent_ids=[PARENT])

    assert graph.member_of["n-match"]["concept_id"] == CHILD
    assert "città" not in classified
    assert "la strada" in classified
    assert stats.reraffine >= 1
    assert stats.generic_instances == 1
    assert graph.nodes[STRADA].get("merged_into")


def test_generic_instances_module_does_not_use_pending_hypothesis():
    source = Path(__file__).resolve().parents[1].joinpath(
        "app/pipeline/generic_instances.py"
    ).read_text(encoding="utf-8")
    assert "pending_hypothesis" not in source
    assert "create_or_reinforce_hypothesis" not in source
    assert "await merge_nodes" not in source
    body = source.split("async def redirect_to_generic", 1)[1]
    assert "PROMOTE_NEWER_SUMMARY" not in body
    judge_src = Path(__file__).resolve().parents[1].joinpath("app/pipeline/judge.py").read_text(
        encoding="utf-8"
    )
    assert "pending_hypothesis" not in judge_src
    assert "create_or_reinforce_hypothesis" not in judge_src
    assert "redirect_to_generic" in judge_src


@pytest.mark.asyncio
async def test_redirect_to_generic_keeps_original_and_skips_summary_promote():
    graph = JudgeGraph()
    graph.add_concept(PARENT, promoted=True, kernel_category="Luogo")
    generic_id = await ensure_generic_instance(graph, PARENT, EntityKernelType.Luogo)
    seeded = "summary aggregato originale"
    graph.nodes[generic_id]["summary"] = seeded
    graph.add_node(STRADA, name="la strada", kernel_category="Luogo", summary="dettaglio")
    graph.set_member_of(STRADA, PARENT)
    graph.add_node(VIANDANTE, name="il viandante")
    graph.add_relation(VIANDANTE, STRADA, relation=FACT_TEXT)

    await redirect_to_generic(graph, STRADA, generic_id)

    assert STRADA in graph.nodes
    assert graph.nodes[STRADA]["merged_into"] == generic_id
    assert graph.nodes[generic_id]["summary"] == seeded
    assert STRADA not in graph.member_of
    assert all(cy != PROMOTE_NEWER_SUMMARY_CYPHER for cy, _kw in graph.calls)
    assert any(rel["src"] == VIANDANTE and rel["dst"] == generic_id for rel in graph.relations)
