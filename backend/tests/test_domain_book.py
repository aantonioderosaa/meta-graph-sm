"""Unit tests for the domain book and formation gates (Fase 1).

Deterministic: no Docker, Neo4j, LLM, or network.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.core.config import Settings
from app.models.kernel import AttributeKernelType, EntityKernelType, RelationKernelType
from app.pipeline.domain_book import (
    CATEGORY_CARDS,
    GENRE_NOT_TOPIC_PROMPT,
    Cluster,
    ClusterCandidate,
    passes_genre_vs_filter_gate,
    passes_mdl_gate,
)

EXPECTED_CARDS: dict[
    EntityKernelType, tuple[frozenset[AttributeKernelType], frozenset[RelationKernelType], str]
] = {
    EntityKernelType.Agente: (
        frozenset(
            {
                AttributeKernelType.Denominazione,
                AttributeKernelType.AttributoTemporale,
                AttributeKernelType.Categoria,
                AttributeKernelType.Quantita,
                AttributeKernelType.Descrizione,
                AttributeKernelType.Stato,
            }
        ),
        frozenset(
            {
                RelationKernelType.Partecipativa,
                RelationKernelType.SocialeIntenzionale,
                RelationKernelType.Causale,
            }
        ),
        "Agente",
    ),
    EntityKernelType.OggettoFisico: (
        frozenset(
            {
                AttributeKernelType.Quantita,
                AttributeKernelType.Categoria,
                AttributeKernelType.AttributoSpaziale,
                AttributeKernelType.Denominazione,
                AttributeKernelType.Stato,
            }
        ),
        frozenset(
            {
                RelationKernelType.Compositiva,
                RelationKernelType.Partecipativa,
                RelationKernelType.Spaziale,
            }
        ),
        "OggettoFisico",
    ),
    EntityKernelType.Luogo: (
        frozenset(
            {
                AttributeKernelType.AttributoSpaziale,
                AttributeKernelType.Denominazione,
                AttributeKernelType.Quantita,
                AttributeKernelType.Categoria,
            }
        ),
        frozenset({RelationKernelType.Spaziale, RelationKernelType.Compositiva}),
        "Luogo",
    ),
    EntityKernelType.Evento: (
        frozenset(
            {
                AttributeKernelType.AttributoTemporale,
                AttributeKernelType.Categoria,
                AttributeKernelType.AttributoSpaziale,
                AttributeKernelType.Stato,
                AttributeKernelType.Descrizione,
            }
        ),
        frozenset(
            {
                RelationKernelType.Partecipativa,
                RelationKernelType.Temporale,
                RelationKernelType.Causale,
            }
        ),
        "Evento",
    ),
    EntityKernelType.EntitaTemporale: (
        frozenset({AttributeKernelType.Quantita, AttributeKernelType.Denominazione}),
        frozenset({RelationKernelType.Temporale}),
        "EntitaTemporale",
    ),
    EntityKernelType.EntitaInformativa: (
        frozenset(
            {
                AttributeKernelType.Denominazione,
                AttributeKernelType.Descrizione,
                AttributeKernelType.AttributoTemporale,
                AttributeKernelType.Categoria,
                AttributeKernelType.Quantita,
            }
        ),
        frozenset(
            {
                RelationKernelType.SocialeIntenzionale,
                RelationKernelType.Compositiva,
            }
        ),
        "EntitaInformativa",
    ),
    EntityKernelType.CostruttoSociale: (
        frozenset(
            {
                AttributeKernelType.Denominazione,
                AttributeKernelType.Categoria,
                AttributeKernelType.AttributoTemporale,
                AttributeKernelType.Quantita,
                AttributeKernelType.Stato,
            }
        ),
        frozenset(
            {
                RelationKernelType.SocialeIntenzionale,
                RelationKernelType.Compositiva,
            }
        ),
        "CostruttoSociale",
    ),
    EntityKernelType.EntitaAstratta: (
        frozenset(
            {
                AttributeKernelType.Denominazione,
                AttributeKernelType.Descrizione,
                AttributeKernelType.Categoria,
            }
        ),
        frozenset({RelationKernelType.SocialeIntenzionale}),
        "EntitaAstratta",
    ),
}


def test_category_cards_cover_exactly_eight_kernel_types():
    assert set(CATEGORY_CARDS.keys()) == set(EntityKernelType)
    assert len(CATEGORY_CARDS) == 8
    for category in EntityKernelType:
        assert category in CATEGORY_CARDS


def test_category_cards_match_ii2_typical_attributes_and_relations():
    for category, (attrs, rels, catch_all) in EXPECTED_CARDS.items():
        card = CATEGORY_CARDS[category]
        assert card.attributi_tipici == attrs, category
        assert card.relazioni_tipiche == rels, category
        assert card.catch_all == catch_all
        assert card.criterio_appartenenza.strip()


def test_genre_vs_filter_sigma_eta_gt_50_giocatori_is_filter():
    candidate = ClusterCandidate(
        definition_kind="value_filter",
        parent_genre="giocatori",
        filter_predicate="età>50",
        kernel_category=EntityKernelType.Agente,
        member_categories=(EntityKernelType.Agente, EntityKernelType.Agente),
    )
    assert passes_genre_vs_filter_gate(candidate) is False


def test_genre_vs_filter_primitive_giocatori_as_agente_passes():
    candidate = ClusterCandidate(
        definition_kind="primitive_concept",
        parent_genre=None,
        filter_predicate=None,
        kernel_category=EntityKernelType.Agente,
        member_categories=(
            EntityKernelType.Agente,
            EntityKernelType.Agente,
            EntityKernelType.Agente,
        ),
    )
    assert passes_genre_vs_filter_gate(candidate) is True


def test_genre_vs_filter_mixed_categories_is_not_homogeneous_genre():
    candidate = ClusterCandidate(
        definition_kind="primitive_concept",
        kernel_category=None,
        member_categories=(
            EntityKernelType.Agente,
            EntityKernelType.Evento,
            EntityKernelType.Luogo,
        ),
    )
    assert passes_genre_vs_filter_gate(candidate) is False


def test_mdl_under_k_always_false_regardless_of_payload():
    cluster = Cluster(
        members=("a", "b"),
        distinct_own_types=frozenset({"tipo_proprio_1", "tipo_proprio_2", "tipo_proprio_3"}),
    )
    assert len(cluster) < 5
    assert len(cluster.distinct_own_types) >= 2
    assert passes_mdl_gate(cluster, k=5, m=2) is False


def test_mdl_coverage_met_but_payload_below_m_is_false():
    cluster = Cluster(
        members=tuple(f"e{i}" for i in range(5)),
        distinct_own_types=frozenset({"solo_uno"}),
    )
    assert passes_mdl_gate(cluster, k=5, m=2) is False


def test_mdl_coverage_and_payload_both_met_is_true():
    cluster = Cluster(
        members=tuple(f"e{i}" for i in range(5)),
        distinct_own_types=frozenset({"tipo_proprio_1", "tipo_proprio_2"}),
    )
    assert passes_mdl_gate(cluster, k=5, m=2) is True


def test_settings_mdl_defaults_are_five_and_two():
    assert Settings.model_fields["BACKBONE_MDL_MIN_COVERAGE"].default == 5
    assert Settings.model_fields["BACKBONE_MDL_MIN_PAYLOAD"].default == 2


def test_genre_not_topic_prompt_is_nonempty_and_states_the_rule():
    assert isinstance(GENRE_NOT_TOPIC_PROMPT, str)
    assert GENRE_NOT_TOPIC_PROMPT.strip()
    lowered = GENRE_NOT_TOPIC_PROMPT.lower()
    assert "genere" in lowered and "omogeneo" in lowered
    assert "argomento" in lowered
    assert "filtro" in lowered and "non crearlo" in lowered


def test_domain_book_does_not_import_neo4j_or_openai():
    source = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "domain_book.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "neo4j" not in imported
    assert "openai" not in imported
