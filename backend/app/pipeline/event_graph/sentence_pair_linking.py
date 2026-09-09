"""MICRO stages 3–4 — typed arcs between sentence heads (Addendum 2).

Stage 3 iterates EVERY adjacent UnitaTesto boundary (i, i+1), never only
where a connective is visible. Stage 4 classifies non-adjacent pairs
(|i-j|>=2) that share an entity. Head→head. Implicit-classifier input is
the raw text of the two units (not extracted nodes). Consumes DedupResult;
does not re-extract; does not change Fusione/Successione/Catena.

Does not implement Allen algebra or ponte.
"""

from __future__ import annotations

from typing import Any

from app.models.event_graph import (
    ArcoEvento,
    ChunkFactsheet,
    EventoRisolto,
    OrientamentoArco,
    PairEdgeDecision,
    PredicatoNonFinito,
    RelazioneSegnale,
    SottoGrafo,
    TipoRelazione,
    ZonaEdgeDecision,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.candidati_entita import condividono_entita
from app.pipeline.event_graph.chunking_periods import UnitaTesto, connettivo_confine
from app.pipeline.event_graph.dedup import DedupResult
from app.pipeline.event_graph.event_edges import (
    COLLEGATO_RELAZIONI,
    RELAZIONE_TO_ARCO,
    VersoRule,
    categorizza,
    tipo_dopo_anti_causa_inventata,
)
from app.pipeline.event_graph.infra.llm import call_structured

REGOLA = "sentence_pair_linking.collega_adiacenti"
REGOLA_NON_ADIACENTI = "sentence_pair_linking.collega_non_adiacenti"

_EVENT_EVENT_TIPI = frozenset(
    {
        "CAUSA",
        "PRECEDE",
        "LIMITE",
        "CONDIZIONE",
        "SCOPO",
        "CONCESSIONE",
        "CONTRASTO",
        "SEQUENZA",
        "CONTENUTO",
        "COLLEGATO",
        "SATELLITE_DI",
    }
)

# Implicit classifier: typed arc only at or above this score.
# Adjacent pairs below it still emit COLLEGATO {segnale: connective or
# "implicito"} so consecutive heads are never structurally isolated.
SOGLIA_CONFIDENZA = 0.5

SYSTEM_PAIR_EDGE = """\
You classify the relation between TWO adjacent text units (Italian or English).
Input is the RAW TEXT of the two units, not extracted event nodes and not a
summary. Signal lives in negation, adverbs, and tense, which extraction
eliminates — use the wording as given.

Return structured output only. Temperature is 0. Never call tools. Never open
a tool loop. No function calling. Structured fields only.

Fields:
- relazione_segnale: exactly one value from the closed micro vocabulary
  (no parallel type system):
  causa_esplicita, consecuzione, posteriorita, anteriorita, limite,
  condizione, scopo, concessione, contrasto, asindeto_sequenziale,
  temporale_ambiguo, gerundio, participio_assoluto, apposizione_relativa,
  due_punti_esplicativo, nessuno.
- orientamento: subordinata_principale | principale_subordinata | coordinata.
  The pair is always given in document order (left unit, then right unit).
  coordinata / subordinata_principale keep left→right for causal verso;
  principale_subordinata flips causal verso (right→left).
- confidenza: float in [0, 1]. Mandatory. Do not omit.
  High (≥0.8) only when the two units clearly support the type.
  Mid (0.5–0.8) when probable. Below 0.5 when weak or speculative.

A connective at the boundary is EVIDENCE, not the label.
Examples: "mentre" / "while" may be temporale_ambiguo OR contrasto;
"e" / "and" may be asindeto_sequenziale OR causa.

Do not invent CAUSA when the link is only sequential or unclear.
Adjacent sentences with no causal connective are sequential
(asindeto_sequenziale / posteriorita), never causa_esplicita.
Prefer nessuno or temporale_ambiguo when unsure.
Do not invent / non inventare. English and Italian.
"""


def user_pair_edge(
    left: UnitaTesto,
    right: UnitaTesto,
    connettivo: str | None,
) -> str:
    if connettivo:
        signal = (
            f"Explicit connective at the boundary of the right unit / "
            f"connettivo esplicito al confine dell'unità destra: {connettivo!r}.\n"
            "Disambiguate the connective (evidence, not a label: e.g. "
            "'mentre' temporal vs adversative, 'e' additive vs causal).\n"
            "Il connettivo è evidenza, non etichetta.\n"
        )
    else:
        signal = (
            "No explicit connective at the boundary. Classify implicitly "
            "from the raw text of the two units. Confidence is mandatory.\n"
            "Assente un connettivo: classificatore implicito sul testo grezzo "
            "delle due unità (non i nodi estratti).\n"
        )
    return (
        "Classify the relation between these two adjacent text units.\n"
        "Classifica la relazione fra queste due unità di testo adiacenti.\n"
        "Use only the raw unit text below. Do not use lemmas or extracted nodes.\n"
        "Non inventare una CAUSA se il nesso è solo sequenziale o incerto.\n\n"
        f"{signal}\n"
        f"unita_sinistra indice={left.indice} tipo={left.tipo}\n"
        f"{left.testo}\n\n"
        f"unita_destra indice={right.indice} tipo={right.tipo}\n"
        f"{right.testo}\n"
    )


def _evento_in_unita(evento: EventoRisolto, unita: UnitaTesto) -> bool:
    start = evento.offset_inizio
    end = evento.offset_fine
    if start is None and end is None:
        return False
    if start is None:
        start = end
    if end is None:
        end = start
    assert start is not None and end is not None
    return start >= unita.offset_inizio and end <= unita.offset_fine


def _pos_key(evento: EventoRisolto) -> tuple[int, int, int, str]:
    return (
        evento.posizione_doc if evento.posizione_doc is not None else -1,
        evento.posizione_chunk if evento.posizione_chunk is not None else -1,
        evento.offset_inizio if evento.offset_inizio is not None else -1,
        evento.id or "",
    )


def _offset_key(evento: EventoRisolto) -> tuple[int, int, str]:
    return (
        evento.offset_inizio if evento.offset_inizio is not None else -1,
        evento.offset_fine if evento.offset_fine is not None else -1,
        evento.id or "",
    )


def testa_della_unita(unita: UnitaTesto, sotto: SottoGrafo) -> EventoRisolto | None:
    """Event with e_testa=True whose span/offsets fall in the unit.

    If several, last by posizione; if none marked testa, last event in the
    unit by offset. Fused events (fuso_in) are ignored.
    """
    in_unit = [
        event
        for event in sotto.eventi
        if not event.fuso_in and _evento_in_unita(event, unita)
    ]
    if not in_unit:
        return None
    teste = [event for event in in_unit if event.e_testa]
    if teste:
        return max(teste, key=_pos_key)
    return max(in_unit, key=_offset_key)


testa_della_unita.__test__ = False  # pytest collects test* names


def _testa_sinistra(
    units: list[UnitaTesto],
    index: int,
    sotto: SottoGrafo,
) -> EventoRisolto | None:
    """Head of the last unit at or before ``index`` that has a testa."""
    for j in range(index, -1, -1):
        head = testa_della_unita(units[j], sotto)
        if head is not None:
            return head
    return None


def _boundary_connettivo(left: UnitaTesto, right: UnitaTesto) -> str | None:
    stored = (right.connettivo_confine or "").strip() or None
    if stored:
        return stored
    return connettivo_confine(right.testo, left.testo)


def _as_decision(parsed: Any) -> PairEdgeDecision:
    if isinstance(parsed, PairEdgeDecision):
        return parsed
    if isinstance(parsed, ZonaEdgeDecision):
        return PairEdgeDecision.model_validate(parsed.model_dump())
    return PairEdgeDecision.model_validate(parsed)


def _apply_verso(
    verso: VersoRule,
    orientamento: OrientamentoArco,
    left: EventoRisolto,
    right: EventoRisolto,
) -> tuple[EventoRisolto, EventoRisolto]:
    """Same verso rules as event_edges, without intra-clause segmentation."""
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
    decision: PairEdgeDecision,
    left: EventoRisolto,
    right: EventoRisolto,
) -> tuple[TipoRelazione, EventoRisolto, EventoRisolto]:
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
    da: EventoRisolto,
    a: EventoRisolto,
    *,
    confidenza: float,
    segnale: str | None,
    regola: str = REGOLA,
) -> ArcoEvento:
    props: dict[str, Any] = {
        "confidenza": confidenza,
        "regola": regola,
        "versione_regole": RULESET_VERSION,
        "livello": "micro",
    }
    if segnale is not None:
        props["segnale"] = segnale
    if tipo == "PRECEDE" and segnale and not str(segnale).startswith("ordine_"):
        props["base"] = "connettivo"
    return ArcoEvento(tipo=tipo, da_id=da.id, a_id=a.id, props=props)


def _arco_from_decision(
    decision: PairEdgeDecision,
    left: EventoRisolto,
    right: EventoRisolto,
    *,
    connettivo: str | None,
    adjacent: bool = True,
    regola: str = REGOLA,
) -> ArcoEvento | None:
    implicit = not connettivo
    if decision.confidenza < SOGLIA_CONFIDENZA:
        return _arco(
            "COLLEGATO",
            left,
            right,
            confidenza=decision.confidenza,
            segnale=connettivo or "implicito",
            regola=regola,
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
    return _arco(tipo, da, a, confidenza=decision.confidenza, segnale=segnale, regola=regola)


async def _decide_pair(
    left: UnitaTesto,
    right: UnitaTesto,
    *,
    connettivo: str | None,
    job_id: str | None,
) -> PairEdgeDecision | None:
    if not (left.testo or "").strip() or not (right.testo or "").strip():
        return None
    try:
        parsed = await call_structured(
            SYSTEM_PAIR_EDGE,
            user_pair_edge(left, right, connettivo),
            PairEdgeDecision,
            temperature=0,
            job_id=job_id,
        )
        return _as_decision(parsed)
    except Exception:
        return None


def _gia_presente(sotto: SottoGrafo, arco: ArcoEvento) -> bool:
    key = (arco.da_id, arco.a_id, arco.tipo)
    return any(
        (existing.da_id, existing.a_id, existing.tipo) == key
        for existing in sotto.archi
    )


def _collegati_evento_evento(sotto: SottoGrafo, id_a: str, id_b: str) -> bool:
    pair = {id_a, id_b}
    for arco in sotto.archi:
        if str(arco.tipo) in _EVENT_EVENT_TIPI and {arco.da_id, arco.a_id} == pair:
            return True
    return False


async def classifica_coppia(
    left_unit: UnitaTesto,
    right_unit: UnitaTesto,
    left_head: EventoRisolto,
    right_head: EventoRisolto,
    *,
    connettivo: str | None = None,
    adjacent: bool = True,
    job_id: str | None = None,
    predicati_non_finiti: list[PredicatoNonFinito] | None = None,
) -> ArcoEvento | None:
    """Classify two unit texts and emit a head→head arc, or None on skip."""
    decision = await _decide_pair(
        left_unit, right_unit, connettivo=connettivo, job_id=job_id
    )
    if decision is None:
        return None
    regola = REGOLA if adjacent else REGOLA_NON_ADIACENTI
    if not adjacent and decision.confidenza < SOGLIA_CONFIDENZA:
        return None
    return _arco_from_decision(
        decision,
        left_head,
        right_head,
        connettivo=connettivo,
        adjacent=adjacent,
        regola=regola,
    )


def _emit_predicati(
    sotto: SottoGrafo,
    predicati_non_finiti: list[PredicatoNonFinito] | None,
) -> None:
    if not predicati_non_finiti:
        return
    extra = categorizza(
        ChunkFactsheet(eventi=[], archi=[], quarantena=[]),
        list(sotto.eventi),
        predicati_non_finiti=predicati_non_finiti,
        menzioni=sotto.menzioni,
    )
    for arco in extra.archi:
        if not _gia_presente(sotto, arco):
            sotto.archi.append(arco)


async def collega_adiacenti(
    dedup: DedupResult, *, job_id: str | None = None,
    predicati_non_finiti: list[PredicatoNonFinito] | None = None,
) -> SottoGrafo:
    """Link every consecutive UnitaTesto pair that has a right testa.

    Visits every boundary (i, i+1), including those whose left side is
    dialogo without a testa: the left head walks to the previous narrativa
    testa. Pairs whose right side has no testa (typically dialogo) are
    skipped after the visit. Mutates and returns ``dedup.sotto``.
    """
    sotto = dedup.sotto
    units = sorted(dedup.unita, key=lambda item: item.indice)
    if len(units) >= 2:
        for i in range(len(units) - 1):
            left_unit, right_unit = units[i], units[i + 1]
            right_head = testa_della_unita(right_unit, sotto)
            if right_head is None:
                continue
            left_head = _testa_sinistra(units, i, sotto)
            if left_head is None or left_head.id == right_head.id:
                continue
            connettivo = _boundary_connettivo(left_unit, right_unit)
            arco = await classifica_coppia(
                left_unit,
                right_unit,
                left_head,
                right_head,
                connettivo=connettivo,
                adjacent=True,
                job_id=job_id,
            )
            if arco is None:
                continue
            if _gia_presente(sotto, arco):
                continue
            sotto.archi.append(arco)

    preds = (
        predicati_non_finiti
        if predicati_non_finiti is not None
        else list(getattr(dedup, "predicati_non_finiti", []) or [])
    )
    _emit_predicati(sotto, preds)
    return sotto


async def collega_non_adiacenti(
    dedup: DedupResult, *, job_id: str | None = None,
    predicati_non_finiti: list[PredicatoNonFinito] | None = None,
) -> SottoGrafo:
    """Classify non-adjacent UnitaTesto pairs that share an entity. Head→head.

    Visits every pair with |i-j|>=2. Adjacent boundaries are stage 3 only.
    Below SOGLIA, or on LLM failure, skip — no COLLEGATO isolation
    placeholder (same policy as zona_edges non-adjacent). Skip if an
    event-event arc already exists between the two head ids.
    """
    sotto = dedup.sotto
    units = sorted(dedup.unita, key=lambda item: item.indice)
    if len(units) < 3:
        return sotto

    for i in range(len(units)):
        for j in range(i + 2, len(units)):
            left_unit, right_unit = units[i], units[j]
            left_head = testa_della_unita(left_unit, sotto)
            right_head = testa_della_unita(right_unit, sotto)
            if left_head is None or right_head is None:
                continue
            if left_head.id == right_head.id:
                continue
            if not condividono_entita(left_head, right_head):
                continue
            if _collegati_evento_evento(sotto, left_head.id, right_head.id):
                continue
            arco = await classifica_coppia(
                left_unit,
                right_unit,
                left_head,
                right_head,
                connettivo=None,
                adjacent=False,
                job_id=job_id,
            )
            if arco is None:
                continue
            if _gia_presente(sotto, arco):
                continue
            sotto.archi.append(arco)

    preds = (
        predicati_non_finiti
        if predicati_non_finiti is not None
        else list(getattr(dedup, "predicati_non_finiti", []) or [])
    )
    _emit_predicati(sotto, preds)
    return sotto


async def collega_inter_frase(
    dedup: DedupResult, *, job_id: str | None = None,
    predicati_non_finiti: list[PredicatoNonFinito] | None = None,
) -> SottoGrafo:
    """Stage 3 then stage 4 on the same DedupResult."""
    await collega_adiacenti(
        dedup, job_id=job_id, predicati_non_finiti=predicati_non_finiti
    )
    return await collega_non_adiacenti(
        dedup, job_id=job_id, predicati_non_finiti=predicati_non_finiti
    )


__all__ = [
    "REGOLA",
    "REGOLA_NON_ADIACENTI",
    "SOGLIA_CONFIDENZA",
    "SYSTEM_PAIR_EDGE",
    "classifica_coppia",
    "collega_adiacenti",
    "collega_inter_frase",
    "collega_non_adiacenti",
    "testa_della_unita",
    "user_pair_edge",
]
