"""Domain book as operational rules (Fase 1).

Declarative category cards (doc2 II.2) and pure formation gates (doc1 §7,
doc4 §1). Testable without Neo4j, LLM, or network. ``GENRE_NOT_TOPIC_PROMPT``
is injected into entity/pair extraction prompts from Fase 3; classification,
dreaming, and PROMOTE hook in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.core.config import settings
from app.models.kernel import AttributeKernelType, EntityKernelType, RelationKernelType

# ---------------------------------------------------------------------------
# Shared prompt block (doc2 II.6) — single source of truth.
# Injected into entity/pair extraction prompts (Fase 3).
# ---------------------------------------------------------------------------

GENRE_NOT_TOPIC_PROMPT = (
    "Un sottodominio è un genere di entità omogeneo: tutti i suoi membri diretti "
    "condividono una categoria fondazionale del kernel (E1–E8). Non è un argomento. "
    "Se stai per creare un dominio che conterrebbe entità di categorie diverse — "
    "persone e eventi e luoghi insieme — fermati: quello è un argomento, non un "
    "dominio. Ogni entità va nella sua categoria; la coerenza tematica non si "
    "rappresenta col contenimento.\n"
    "\n"
    "Non elencare i domini in anticipo. Classifica ogni entità in una categoria "
    "del kernel; i sottodomini emergeranno quando abbastanza entità dimostreranno "
    "un genere comune. Prima di creare un dominio applica il test: è definito da "
    "un tipo di cosa (genere) o da un predicato sui valori / dall'argomento di cui "
    "parla (filtro)? Se è un filtro, non crearlo."
)


@dataclass(frozen=True)
class CategoryCard:
    """Admission rules for one kernel category (doc2 II.2)."""

    attributi_tipici: frozenset[AttributeKernelType]
    relazioni_tipiche: frozenset[RelationKernelType]
    criterio_appartenenza: str
    catch_all: str
    notes: str = ""


CATEGORY_CARDS: dict[EntityKernelType, CategoryCard] = {
    EntityKernelType.Agente: CategoryCard(
        attributi_tipici=frozenset(
            {
                AttributeKernelType.Denominazione,
                AttributeKernelType.AttributoTemporale,
                AttributeKernelType.Categoria,
                AttributeKernelType.Quantita,
                AttributeKernelType.Descrizione,
                AttributeKernelType.Stato,
            }
        ),
        relazioni_tipiche=frozenset(
            {
                RelationKernelType.Partecipativa,
                RelationKernelType.SocialeIntenzionale,
                RelationKernelType.Causale,
            }
        ),
        criterio_appartenenza="Può iniziare/causare azioni → «chi agisce?»",
        catch_all="Agente",
        notes="Faccetta: Organizzazione = E1+E7",
    ),
    EntityKernelType.OggettoFisico: CategoryCard(
        attributi_tipici=frozenset(
            {
                AttributeKernelType.Quantita,
                AttributeKernelType.Categoria,
                AttributeKernelType.AttributoSpaziale,
                AttributeKernelType.Denominazione,
                AttributeKernelType.Stato,
            }
        ),
        relazioni_tipiche=frozenset(
            {
                RelationKernelType.Compositiva,
                RelationKernelType.Partecipativa,
                RelationKernelType.Spaziale,
            }
        ),
        criterio_appartenenza="Materiale, senza agency → «che cos'è, fisicamente?»",
        catch_all="OggettoFisico",
    ),
    EntityKernelType.Luogo: CategoryCard(
        attributi_tipici=frozenset(
            {
                AttributeKernelType.AttributoSpaziale,
                AttributeKernelType.Denominazione,
                AttributeKernelType.Quantita,
                AttributeKernelType.Categoria,
            }
        ),
        relazioni_tipiche=frozenset(
            {
                RelationKernelType.Spaziale,
                RelationKernelType.Compositiva,
            }
        ),
        criterio_appartenenza="Regione spaziale / feature → «dove?»",
        catch_all="Luogo",
    ),
    EntityKernelType.Evento: CategoryCard(
        attributi_tipici=frozenset(
            {
                AttributeKernelType.AttributoTemporale,
                AttributeKernelType.Categoria,
                AttributeKernelType.AttributoSpaziale,
                AttributeKernelType.Stato,
                AttributeKernelType.Descrizione,
            }
        ),
        relazioni_tipiche=frozenset(
            {
                RelationKernelType.Partecipativa,
                RelationKernelType.Temporale,
                RelationKernelType.Causale,
            }
        ),
        criterio_appartenenza="Accade nel tempo → «che cosa succede?»",
        catch_all="Evento",
        notes="Nodo-collante contestuale: è qui che si reifica la situazione condivisa",
    ),
    EntityKernelType.EntitaTemporale: CategoryCard(
        attributi_tipici=frozenset(
            {
                AttributeKernelType.Quantita,
                AttributeKernelType.Denominazione,
            }
        ),
        relazioni_tipiche=frozenset({RelationKernelType.Temporale}),
        criterio_appartenenza="Il tempo reificato come oggetto di prima classe",
        catch_all="EntitaTemporale",
        notes=(
            "Da usare solo quando serve puntare un tempo; "
            "altrimenti il tempo è attributo (A4)"
        ),
    ),
    EntityKernelType.EntitaInformativa: CategoryCard(
        attributi_tipici=frozenset(
            {
                AttributeKernelType.Denominazione,
                AttributeKernelType.Descrizione,
                AttributeKernelType.AttributoTemporale,
                AttributeKernelType.Categoria,
                AttributeKernelType.Quantita,
            }
        ),
        relazioni_tipiche=frozenset(
            {
                RelationKernelType.SocialeIntenzionale,
                RelationKernelType.Compositiva,
            }
        ),
        criterio_appartenenza=(
            "Contenuto che «parla di» altro, con copie multiple → "
            "«quale documento/opera?»"
        ),
        catch_all="EntitaInformativa",
        notes="Le fonti del corpus sono E6; spesso bersaglio di derived_from (Fam. B)",
    ),
    EntityKernelType.CostruttoSociale: CategoryCard(
        attributi_tipici=frozenset(
            {
                AttributeKernelType.Denominazione,
                AttributeKernelType.Categoria,
                AttributeKernelType.AttributoTemporale,
                AttributeKernelType.Quantita,
                AttributeKernelType.Stato,
            }
        ),
        relazioni_tipiche=frozenset(
            {
                RelationKernelType.SocialeIntenzionale,
                RelationKernelType.Compositiva,
            }
        ),
        criterio_appartenenza=(
            "Esiste per convenzione/accordo collettivo → "
            "«esiste perché lo riconosciamo?»"
        ),
        catch_all="CostruttoSociale",
        notes="Faccette: ruolo, titolo, istituzione",
    ),
    EntityKernelType.EntitaAstratta: CategoryCard(
        attributi_tipici=frozenset(
            {
                AttributeKernelType.Denominazione,
                AttributeKernelType.Descrizione,
                AttributeKernelType.Categoria,
            }
        ),
        relazioni_tipiche=frozenset({RelationKernelType.SocialeIntenzionale}),
        criterio_appartenenza=(
            "Nessuna estensione spazio-temporale → «esiste come idea/valore?»"
        ),
        catch_all="EntitaAstratta",
        notes=(
            "Non confondere il concetto (E8, oggetto del discorso) "
            "con il tipo del reticolo"
        ),
    ),
}


# ---------------------------------------------------------------------------
# Genre-vs-filter gate (doc1 §7 (a); doc2 II.1 / II.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusterCandidate:
    """Structured candidate for the genre-vs-filter gate. No LLM involved.

    ``definition_kind="value_filter"`` is the σ_predicato(genere_esistente) case
    (e.g. σ_età>50(giocatori)). ``"primitive_concept"`` is a homogeneous kind
    definable from a primitive (e.g. giocatori as a kind of Agente).
    """

    definition_kind: Literal["primitive_concept", "value_filter"]
    parent_genre: str | None = None
    filter_predicate: str | None = None
    kernel_category: EntityKernelType | None = None
    member_categories: tuple[EntityKernelType, ...] = ()


def passes_genre_vs_filter_gate(candidate: ClusterCandidate) -> bool:
    """Return True iff the candidate is a homogeneous primitive genre, not a filter.

    Fail-closed: mixed kernel categories among members are not a homogeneous
    genre (doc2 II.1 rule 3 / II.6).
    """
    if candidate.definition_kind != "primitive_concept":
        return False
    distinct_members = frozenset(candidate.member_categories)
    if len(distinct_members) > 1:
        return False
    if (
        candidate.kernel_category is not None
        and distinct_members
        and candidate.kernel_category not in distinct_members
    ):
        return False
    return True


# ---------------------------------------------------------------------------
# MDL two-threshold gate (doc4 §1) — coverage AND payload, not OR
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cluster:
    """Cluster scored by the two-threshold MDL baseline.

    ``members`` is the instance set (coverage = ``len(cluster)``).
    ``distinct_own_types`` are own (non-inherited) type/attribute/relation names.
    """

    members: tuple[object, ...] = ()
    distinct_own_types: frozenset[str] = field(default_factory=frozenset)

    def __len__(self) -> int:
        return len(self.members)


def passes_mdl_gate(
    cluster: Cluster,
    k: int | None = None,
    m: int | None = None,
) -> bool:
    """Return True iff coverage ≥ k AND payload ≥ m (doc4 §1).

    Defaults come from Settings when ``k`` / ``m`` are omitted. Tests should
    pass explicit thresholds to stay deterministic without depending on env.
    """
    coverage_k = settings.BACKBONE_MDL_MIN_COVERAGE if k is None else k
    payload_m = settings.BACKBONE_MDL_MIN_PAYLOAD if m is None else m
    return len(cluster) >= coverage_k and len(cluster.distinct_own_types) >= payload_m
