"""M2 — typed arcs between :Zona, same closed vocab as micro event-event edges.

Input is zone summaries (riassunto), not raw text. Declared less reliable:
every arc carries ``confidenza`` and ``verificato=None`` until the bridge
(M-ponte) sets the latter. Leaves ``su_via_principale`` empty (M-macro3 annotates it).
Does not materialize CAUSA⇒PRECEDE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.models.event_graph import (
    OrientamentoArco,
    RelazioneSegnale,
    TipoRelazione,
    ZonaEdgeDecision,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.chunking_periods import connettivo_confine
from app.pipeline.event_graph.event_edges import (
    COLLEGATO_RELAZIONI,
    RELAZIONE_TO_ARCO,
    SEGNALI_LIVELLO_TEMPORALE,
    VersoRule,
    tipo_dopo_anti_causa_inventata,
)
from app.pipeline.event_graph.infra.llm import call_structured
from app.pipeline.event_graph.zona_segmentation import Zona

REGOLA = "zona_edges.collega"

# Zona↔Zona succession (Livello 1). Independent of ArcoZona / collega_zone /
# TipoRelazione (that enum is Evento↔Evento/Menzione).
SUCCESSIONE_ZONA = "SUCCESSIONE_ZONA"

# Implicit classifier: typed arc only at or above this score.
# Adjacent pairs below it still emit COLLEGATO {segnale: "implicito"} so
# consecutive zones are never structurally isolated. Non-adjacent pairs
# below the threshold are skipped (no isolation placeholder).
SOGLIA_CONFIDENZA = 0.5

SYSTEM_ZONA_EDGE = """\
You classify the relation between TWO zone summaries (Italian or English).
Input is the riassunto of each zone, not raw source text. Be conservative.

Return structured output only. Temperature is 0. Never call tools. Never open
a tool loop. No function calling. Structured fields only.

Fields:
- relazione_segnale: exactly one value from the closed micro vocabulary
  (no parallel type system):
  causa_esplicita, consecuzione, limite,
  condizione, scopo, concessione, contrasto, asindeto_sequenziale,
  temporale_ambiguo, gerundio, participio_assoluto, apposizione_relativa,
  due_punti_esplicativo, nessuno.
  Do not use posteriorita or anteriorita: chronological before/after belongs
  to the later temporal pass, not this stage.
- orientamento: subordinata_principale | principale_subordinata | coordinata.
  The pair is always given in document order (left zone, then right zone).
  coordinata / subordinata_principale keep left→right for causal verso;
  principale_subordinata flips causal verso (right→left).
- confidenza: float in [0, 1]. Mandatory. Do not omit.
  High (≥0.8) only when the summaries clearly support the type.
  Mid (0.5–0.8) when probable. Below 0.5 when weak or speculative.

Do not invent CAUSA when the link is only sequential or unclear.
Adjacent narrative zones with no causal connective (perché, quindi, perciò,
because, therefore) are sequential: use asindeto_sequenziale,
never causa_esplicita. CAUSA is only for an explicit causal/result connective.
If the only link is before/after (poi, prima, dopo, then, after), use
nessuno — do not assign posteriorita or anteriorita here.
Prefer nessuno or temporale_ambiguo when unsure.
Do not invent / non inventare. English and Italian.
"""


@dataclass
class ArcoZona:
    da_id: str
    a_id: str
    tipo: TipoRelazione | str
    livello: Literal["macro"] = "macro"
    confidenza: float = 0.0
    verificato: bool | None = None
    segnale: str | None = None
    regola: str = REGOLA
    versione_regole: str = RULESET_VERSION
    su_via_principale: list[str] = field(default_factory=list)


def user_zona_edge(
    left: Zona,
    right: Zona,
    left_text: str,
    right_text: str,
    connettivo: str | None,
) -> str:
    if connettivo:
        signal = (
            f"Explicit connective at the boundary of the right zone / "
            f"connettivo esplicito al confine della zona destra: {connettivo!r}.\n"
            "Disambiguate the connective (evidence, not a label: e.g. "
            "'mentre' temporal vs adversative, 'e' additive vs causal).\n"
        )
    else:
        signal = (
            "No explicit connective at the boundary. Classify implicitly "
            "from the two summaries. Confidence is mandatory.\n"
            "Assente un connettivo: classificatore implicito sui riassunti.\n"
        )
    return (
        "Classify the relation between these two zone summaries.\n"
        "Classifica la relazione fra questi due riassunti di zona.\n"
        "Use only the summaries (fallback text if a summary is empty).\n"
        "Non inventare una CAUSA se il nesso è solo sequenziale o incerto.\n\n"
        f"{signal}\n"
        f"zona_sinistra id={left.id} ordinale={left.ordinale}\n"
        f"{left_text}\n\n"
        f"zona_destra id={right.id} ordinale={right.ordinale}\n"
        f"{right_text}\n"
    )


def _zona_input(zona: Zona) -> str:
    summary = (zona.riassunto or "").strip()
    if summary:
        return summary
    return (zona.testo or "").strip()


def _entita_norm(items: list[str]) -> set[str]:
    return {item.strip().casefold() for item in items if item and item.strip()}


def _condividono_entita(left: Zona, right: Zona) -> bool:
    return bool(_entita_norm(left.entita_principali) & _entita_norm(right.entita_principali))


def _as_decision(parsed: Any) -> ZonaEdgeDecision:
    if isinstance(parsed, ZonaEdgeDecision):
        return parsed
    return ZonaEdgeDecision.model_validate(parsed)


def _apply_verso(
    verso: VersoRule,
    orientamento: OrientamentoArco,
    left: Zona,
    right: Zona,
) -> tuple[Zona, Zona]:
    """Same verso rules as event_edges, without clause segmentation."""
    if verso == "reverse":
        return right, left
    if verso == "keep":
        return left, right
    if orientamento == "coordinata":
        return left, right
    if orientamento == "principale_subordinata":
        return right, left
    return left, right


def _tipo_e_estremi(
    decision: ZonaEdgeDecision,
    left: Zona,
    right: Zona,
) -> tuple[TipoRelazione, Zona, Zona]:
    signal: RelazioneSegnale = decision.relazione_segnale
    if signal in COLLEGATO_RELAZIONI:
        return "COLLEGATO", left, right
    mapped = RELAZIONE_TO_ARCO.get(signal)
    if mapped is None:
        return "COLLEGATO", left, right
    tipo, verso = mapped
    da, a = _apply_verso(verso, decision.orientamento, left, right)
    return tipo, da, a


def _arco(
    tipo: TipoRelazione | str,
    da: Zona,
    a: Zona,
    *,
    confidenza: float,
    segnale: str | None,
) -> ArcoZona:
    return ArcoZona(
        da_id=da.id,
        a_id=a.id,
        tipo=tipo,
        livello="macro",
        confidenza=confidenza,
        verificato=None,
        segnale=segnale,
        regola=REGOLA,
        versione_regole=RULESET_VERSION,
    )


async def _decide_pair(
    left: Zona,
    right: Zona,
    *,
    connettivo: str | None,
    job_id: str | None,
) -> ZonaEdgeDecision | None:
    left_text = _zona_input(left)
    right_text = _zona_input(right)
    if not left_text or not right_text:
        return None
    try:
        parsed = await call_structured(
            SYSTEM_ZONA_EDGE,
            user_zona_edge(left, right, left_text, right_text, connettivo),
            ZonaEdgeDecision,
            temperature=0,
            job_id=job_id,
        )
        return _as_decision(parsed)
    except Exception:
        return None


def _arco_from_decision(
    decision: ZonaEdgeDecision,
    left: Zona,
    right: Zona,
    *,
    adjacent: bool,
    connettivo: str | None,
) -> ArcoZona | None:
    if decision.relazione_segnale in SEGNALI_LIVELLO_TEMPORALE:
        return None
    implicit = not connettivo
    if implicit and decision.confidenza < SOGLIA_CONFIDENZA:
        if not adjacent:
            return None
        return _arco(
            "COLLEGATO",
            left,
            right,
            confidenza=decision.confidenza,
            segnale="implicito",
        )
    tipo, da, a = _tipo_e_estremi(decision, left, right)
    tipo = tipo_dopo_anti_causa_inventata(
        tipo, adjacent=adjacent, connettivo=connettivo
    )
    if tipo is None:
        return None
    if implicit and tipo == "COLLEGATO":
        segnale: str | None = "implicito"
    else:
        segnale = connettivo
    return _arco(tipo, da, a, confidenza=decision.confidenza, segnale=segnale)


async def collega_zone(
    zone: list[Zona], *, job_id: str | None = None
) -> list[ArcoZona]:
    """Link zones: every adjacent pair, plus non-adjacent pairs that share entities."""
    ordered = sorted(zone, key=lambda item: item.ordinale)
    if len(ordered) < 2:
        return []

    archi: list[ArcoZona] = []
    seen: set[frozenset[str]] = set()

    for i in range(len(ordered) - 1):
        left, right = ordered[i], ordered[i + 1]
        key = frozenset({left.id, right.id})
        if key in seen or left.id == right.id:
            continue
        connettivo = connettivo_confine(right.testo, left.testo)
        decision = await _decide_pair(
            left, right, connettivo=connettivo, job_id=job_id
        )
        if decision is None:
            continue
        arco = _arco_from_decision(
            decision, left, right, adjacent=True, connettivo=connettivo
        )
        if arco is None:
            continue
        archi.append(arco)
        seen.add(key)

    for i in range(len(ordered)):
        for j in range(i + 2, len(ordered)):
            left, right = ordered[i], ordered[j]
            if not _condividono_entita(left, right):
                continue
            key = frozenset({left.id, right.id})
            if key in seen or left.id == right.id:
                continue
            decision = await _decide_pair(
                left, right, connettivo=None, job_id=job_id
            )
            if decision is None:
                continue
            arco = _arco_from_decision(
                decision, left, right, adjacent=False, connettivo=None
            )
            if arco is None:
                continue
            archi.append(arco)
            seen.add(key)

    return archi


__all__ = [
    "REGOLA",
    "SOGLIA_CONFIDENZA",
    "SUCCESSIONE_ZONA",
    "SYSTEM_ZONA_EDGE",
    "ArcoZona",
    "collega_zone",
    "user_zona_edge",
]
