"""Closed Metagraph kernel vocabulary (three axes + Famiglia B + backbone).

This module *declares* the closed vocabulary. Ingestion (Fase 3) uses
``EntityKernelType`` / ``RelationKernelType`` for constrained decoding; later
phases consume the same types for backbone, dreaming, and Neo4j writes.

Source of truth:
- form/function: ``1.metagraph-formalizzazione-rev.md`` §3
- populated lists: ``2.metagraph-kernel-tre-assi (2).md`` Parte I

**No ninth horizontal primitive** may be added without a presided human revision
of the kernel (doc2 principle 2 / §8; cf. doc1 §12 completeness of the upper
ontology). Anyone adding a primitive must find this constraint here, in the file
that would otherwise be edited to grow the vertex horizontally. Growth is only
vertical: refinements hang ``is_a`` *under* an existing maximum element.

``KERNEL_VERSION`` is the versioned constant to write on every ``:Concept``
promoted to a genre from Fase 4/5 onward. It is declared here; it is not written
to Neo4j in this phase.
"""

from __future__ import annotations

from enum import Enum, unique

KERNEL_VERSION = "1.0.0"

# Backbone relations: not Famiglia A, not Famiglia B. Frozen invariants of the
# type lattice (``is_a``) and the instance tree (``member_of``). See doc2
# "Relazioni-backbone" and doc1 §4.1.
IS_A = "is_a"
MEMBER_OF = "member_of"


@unique
class EntityKernelType(str, Enum):
    """Foundational entity categories (Asse 1, E1–E8).

    A node holds exactly one of these values, never a combination. Cross-category
    referents (e.g. Agente + CostruttoSociale) are handled later via identity +
    facets, not by combining enum members.
    """

    Agente = "Agente"  # E1
    OggettoFisico = "OggettoFisico"  # E2
    Luogo = "Luogo"  # E3
    Evento = "Evento"  # E4
    EntitaTemporale = "EntitaTemporale"  # E5
    EntitaInformativa = "EntitaInformativa"  # E6
    CostruttoSociale = "CostruttoSociale"  # E7
    EntitaAstratta = "EntitaAstratta"  # E8


@unique
class AttributeKernelType(str, Enum):
    """Attribute categories of entities (Asse 2, A1–A7).

    An attribute describes a single entity. A property that involves a second
    entity is a relation, not an attribute.
    """

    Denominazione = "Denominazione"  # A1
    Quantita = "Quantita"  # A2
    Categoria = "Categoria"  # A3
    AttributoTemporale = "AttributoTemporale"  # A4
    AttributoSpaziale = "AttributoSpaziale"  # A5
    Descrizione = "Descrizione"  # A6
    Stato = "Stato"  # A7


@unique
class RelationKernelType(str, Enum):
    """Famiglia A — maximal semantic relation primitives (Asse 3, R1–R6).

    These six are the only maximum elements. Concrete refinements
    (``coached_by``, ``plays_for``, ``hasEngine``, …) stay free strings in later
    phases and must carry ``kernel_parent`` pointing at exactly one of these
    members. They are never siblings of R1–R6.
    """

    Compositiva = "Compositiva"  # R1
    Spaziale = "Spaziale"  # R2
    Temporale = "Temporale"  # R3
    Causale = "Causale"  # R4
    Partecipativa = "Partecipativa"  # R5
    SocialeIntenzionale = "SocialeIntenzionale"  # R6


@unique
class SpecialRelationType(str, Enum):
    """Famiglia B — frozen structural/meta relations (epistemic bookkeeping).

    These describe the KB's epistemic state, not the world. The set is frozen:
    never refined, never re-typed per domain, and ``PROMOTE`` does not touch it
    (doc2 §11.2 / Famiglia B; doc1 §11.2). ``updated_by`` is a distinct member
    even though doc2 groups ``supersedes`` / ``updated_by`` together as M4.
    """

    same_as = "same_as"
    possibly_same_as = "possibly_same_as"
    contradicts = "contradicts"
    supersedes = "supersedes"
    updated_by = "updated_by"
    derived_from = "derived_from"
    equivalent_to = "equivalent_to"
