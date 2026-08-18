"""Regression tests for the closed Metagraph kernel vocabulary (Fase 0)."""

from __future__ import annotations

from enum import Flag, IntFlag

import pytest

from app.models.kernel import (
    IS_A,
    KERNEL_VERSION,
    MEMBER_OF,
    AttributeKernelType,
    EntityKernelType,
    RelationKernelType,
    SpecialRelationType,
)


def test_entity_kernel_type_exact_set():
    assert len(EntityKernelType) == 8
    assert {m.name: m.value for m in EntityKernelType} == {
        "Agente": "Agente",
        "OggettoFisico": "OggettoFisico",
        "Luogo": "Luogo",
        "Evento": "Evento",
        "EntitaTemporale": "EntitaTemporale",
        "EntitaInformativa": "EntitaInformativa",
        "CostruttoSociale": "CostruttoSociale",
        "EntitaAstratta": "EntitaAstratta",
    }


def test_attribute_kernel_type_exact_set():
    assert len(AttributeKernelType) == 7
    assert {m.name: m.value for m in AttributeKernelType} == {
        "Denominazione": "Denominazione",
        "Quantita": "Quantita",
        "Categoria": "Categoria",
        "AttributoTemporale": "AttributoTemporale",
        "AttributoSpaziale": "AttributoSpaziale",
        "Descrizione": "Descrizione",
        "Stato": "Stato",
    }


def test_relation_kernel_type_exact_set():
    assert len(RelationKernelType) == 6
    assert {m.name: m.value for m in RelationKernelType} == {
        "Compositiva": "Compositiva",
        "Spaziale": "Spaziale",
        "Temporale": "Temporale",
        "Causale": "Causale",
        "Partecipativa": "Partecipativa",
        "SocialeIntenzionale": "SocialeIntenzionale",
    }


def test_special_relation_type_exact_set():
    assert len(SpecialRelationType) == 7
    assert {m.name: m.value for m in SpecialRelationType} == {
        "same_as": "same_as",
        "possibly_same_as": "possibly_same_as",
        "contradicts": "contradicts",
        "supersedes": "supersedes",
        "updated_by": "updated_by",
        "derived_from": "derived_from",
        "equivalent_to": "equivalent_to",
    }


def test_kernel_version_constant():
    assert KERNEL_VERSION == "1.0.0"


def test_backbone_relation_constants():
    assert IS_A == "is_a"
    assert MEMBER_OF == "member_of"
    assert IS_A not in {m.value for m in RelationKernelType}
    assert MEMBER_OF not in {m.value for m in RelationKernelType}
    assert IS_A not in {m.value for m in SpecialRelationType}
    assert MEMBER_OF not in {m.value for m in SpecialRelationType}


def test_entity_kernel_type_not_combinable():
    """A node holds a single EntityKernelType, never a combination of members."""
    assert not issubclass(EntityKernelType, Flag)
    assert not issubclass(EntityKernelType, IntFlag)
    combined = f"{EntityKernelType.Agente.value}+{EntityKernelType.CostruttoSociale.value}"
    with pytest.raises(ValueError):
        EntityKernelType(combined)


def test_kernel_public_names_exported_from_models():
    import app.models as models

    assert models.EntityKernelType is EntityKernelType
    assert models.AttributeKernelType is AttributeKernelType
    assert models.RelationKernelType is RelationKernelType
    assert models.SpecialRelationType is SpecialRelationType
    assert models.KERNEL_VERSION == KERNEL_VERSION
    assert models.IS_A == IS_A
    assert models.MEMBER_OF == MEMBER_OF
