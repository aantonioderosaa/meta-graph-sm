"""§8 — relazione_segnale → typed event edges, COLLEGATO, CONTENUTO, §8.4.

Does not materialize CAUSA⇒PRECEDE. Does not pack document-level SEQUENZA (M7).
``asindeto_sequenziale`` may emit a typed SEQUENZA from the dict — that is M5.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from app.models.event_graph import (
    ArcoEvento,
    ArcoEventoGrezzo,
    ChunkFactsheet,
    EventoRisolto,
    MenzioneRisolta,
    OrientamentoArco,
    PredicatoNonFinito,
    QuarantenaItem,
    RelazioneSegnale,
    TipoRelazione,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.ids import quarantena_id

REGOLA = "event_edges.categorizza"

VersoRule = Literal["causa", "keep", "reverse"]

# Fixed dict: relazione_segnale → (tipo_arco, verso_rule). CONTENUTO is not here.
RELAZIONE_TO_ARCO: dict[RelazioneSegnale, tuple[TipoRelazione, VersoRule]] = {
    "causa_esplicita": ("CAUSA", "causa"),
    "consecuzione": ("CAUSA", "keep"),
    "posteriorita": ("PRECEDE", "keep"),
    "anteriorita": ("PRECEDE", "reverse"),
    "limite": ("LIMITE", "causa"),
    "condizione": ("CONDIZIONE", "causa"),
    "scopo": ("SCOPO", "causa"),
    "concessione": ("CONCESSIONE", "causa"),
    "contrasto": ("CONTRASTO", "causa"),
    "asindeto_sequenziale": ("SEQUENZA", "keep"),
}

# Surface tokens that license CAUSA. Without one of these, a classifier
# label of causa_esplicita / consecuzione is sequential story order, not cause.
_CAUSAL_SURFACE = frozenset(
    {
        "because",
        "consequently",
        "così",
        "cosi",
        "dato che",
        "dunque",
        "hence",
        "in quanto",
        "perché",
        "perchè",
        "perche",
        "perciò",
        "percio",
        "pertanto",
        "poiché",
        "poichè",
        "quindi",
        "siccome",
        "since",
        "so",
        "therefore",
        "thus",
    }
)


def is_causal_connective(connettivo: str | None) -> bool:
    if not connettivo:
        return False
    return connettivo.strip().casefold() in _CAUSAL_SURFACE


def tipo_dopo_anti_causa_inventata(
    tipo: TipoRelazione | str,
    *,
    adjacent: bool,
    connettivo: str | None,
) -> TipoRelazione | str | None:
    """Drop invented CAUSA: keep it only with an explicit causal connective."""
    if tipo != "CAUSA":
        return tipo
    if is_causal_connective(connettivo):
        return "CAUSA"
    if adjacent:
        return "SEQUENZA"
    return None


COLLEGATO_RELAZIONI: frozenset[RelazioneSegnale] = frozenset(
    {
        "temporale_ambiguo",
        "gerundio",
        "participio_assoluto",
        "apposizione_relativa",
        "due_punti_esplicativo",
        "nessuno",
    }
)

_SUBORDINATE_SEGS = frozenset(
    {
        "subordinata_finita",
        "infinitiva",
        "gerundiva",
        "participiale",
        "implicita",
        "nominale",
    }
)


@dataclass
class EventEdgesResult:
    archi: list[ArcoEvento] = field(default_factory=list)
    quarantena: list[QuarantenaItem] = field(default_factory=list)


def categorizza(
    factsheet: ChunkFactsheet,
    eventi: list[EventoRisolto],
    testo_chunk: str = "",
    predicati_non_finiti: list[PredicatoNonFinito] | None = None,
    menzioni: dict[str, MenzioneRisolta] | list[MenzioneRisolta] | None = None,
) -> EventEdgesResult:
    """Map grezzo connective arches onto resolved events and emit CONTENUTO."""
    by_indice = _index_by_grezzo(eventi)
    result = EventEdgesResult()

    for grezzo in factsheet.archi:
        da = by_indice.get(grezzo.da_indice)
        a = by_indice.get(grezzo.a_indice)
        if da is None or a is None:
            continue
        if da.id == a.id:
            continue
        if not _visible_signal(grezzo, da, a):
            continue
        if not _same_nesting(da, a):
            continue

        tipo, da_ev, a_ev = _tipo_e_estremi(grezzo, da, a)
        if da_ev.id == a_ev.id:
            continue
        if tipo == "CAUSA" and _causa_reaches(result.archi, a_ev.id, da_ev.id):
            result.quarantena.append(
                _ciclo_causa_item(eventi, grezzo, da_ev, a_ev, testo_chunk)
            )
            continue
        result.archi.append(_arco(tipo, da_ev, a_ev, grezzo))

    _emit_contenuto(eventi, by_indice, result)
    _emit_predicati_non_finiti(
        predicati_non_finiti or list(getattr(factsheet, "predicati_non_finiti", []) or []),
        eventi,
        menzioni,
        result,
    )
    return result


def _index_by_grezzo(eventi: list[EventoRisolto]) -> dict[int, EventoRisolto]:
    by_indice: dict[int, EventoRisolto] = {}
    for event in eventi:
        if event.indice_grezzo is not None and event.indice_grezzo not in by_indice:
            by_indice[event.indice_grezzo] = event
    return by_indice


def _visible_signal(
    grezzo: ArcoEventoGrezzo,
    da: EventoRisolto,
    a: EventoRisolto,
) -> bool:
    if grezzo.segnale_testuale.strip() != "":
        return True
    if grezzo.relazione_segnale != "asindeto_sequenziale":
        return False
    if da.piano != "PRIMO_PIANO" or a.piano != "PRIMO_PIANO":
        return False
    if da.posizione_chunk is None or a.posizione_chunk is None:
        return False
    return abs(da.posizione_chunk - a.posizione_chunk) == 1


def _same_nesting(da: EventoRisolto, a: EventoRisolto) -> bool:
    if da.completiva_di is None and a.completiva_di is None:
        return True
    if da.completiva_di is not None and da.completiva_di == a.completiva_di:
        return True
    if a.indice_grezzo is not None and da.completiva_di == a.indice_grezzo:
        return True
    if da.indice_grezzo is not None and a.completiva_di == da.indice_grezzo:
        return True
    return False


def _tipo_e_estremi(
    grezzo: ArcoEventoGrezzo,
    da: EventoRisolto,
    a: EventoRisolto,
) -> tuple[TipoRelazione, EventoRisolto, EventoRisolto]:
    if grezzo.relazione_segnale in COLLEGATO_RELAZIONI:
        return "COLLEGATO", da, a
    mapped = RELAZIONE_TO_ARCO.get(grezzo.relazione_segnale)
    if mapped is None:
        return "COLLEGATO", da, a
    tipo, verso = mapped
    da_ev, a_ev = _apply_verso(verso, grezzo.orientamento, da, a)
    return tipo, da_ev, a_ev


def _apply_verso(
    verso: VersoRule,
    orientamento: OrientamentoArco,
    da: EventoRisolto,
    a: EventoRisolto,
) -> tuple[EventoRisolto, EventoRisolto]:
    if verso == "reverse":
        return a, da
    if verso == "keep":
        return da, a
    return _verso_causa(orientamento, da, a)


def _is_subordinata(event: EventoRisolto) -> bool:
    return event.segmentazione in _SUBORDINATE_SEGS


def _is_principale(event: EventoRisolto) -> bool:
    return event.segmentazione == "principale_finita"


def _verso_causa(
    orientamento: OrientamentoArco,
    da: EventoRisolto,
    a: EventoRisolto,
) -> tuple[EventoRisolto, EventoRisolto]:
    """sub→main (subordinata_principale) or main→sub (principale_subordinata)."""
    if orientamento == "coordinata":
        return da, a

    sub: EventoRisolto | None = None
    main: EventoRisolto | None = None
    if _is_subordinata(da) and _is_principale(a):
        sub, main = da, a
    elif _is_subordinata(a) and _is_principale(da):
        sub, main = a, da

    if orientamento == "principale_subordinata":
        if sub is not None and main is not None:
            return main, sub
        return a, da
    if sub is not None and main is not None:
        return sub, main
    return da, a


def _base_props(grezzo: ArcoEventoGrezzo, tipo: TipoRelazione) -> dict[str, str]:
    props: dict[str, str] = {
        "regola": REGOLA,
        "versione_regole": RULESET_VERSION,
    }
    if tipo == "PRECEDE":
        props["base"] = "connettivo"
    if tipo == "COLLEGATO":
        props["segnale"] = grezzo.segnale_testuale
    return props


def _arco(
    tipo: TipoRelazione,
    da: EventoRisolto,
    a: EventoRisolto,
    grezzo: ArcoEventoGrezzo,
) -> ArcoEvento:
    return ArcoEvento(
        tipo=tipo,
        da_id=da.id,
        a_id=a.id,
        props=_base_props(grezzo, tipo),
    )


def _causa_reaches(archi: list[ArcoEvento], start_id: str, goal_id: str) -> bool:
    graph: dict[str, list[str]] = defaultdict(list)
    for arco in archi:
        if arco.tipo == "CAUSA":
            graph[arco.da_id].append(arco.a_id)
    seen: set[str] = set()
    stack = [start_id]
    while stack:
        node = stack.pop()
        if node == goal_id:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph[node])
    return False


def _ciclo_causa_item(
    eventi: list[EventoRisolto],
    grezzo: ArcoEventoGrezzo,
    da: EventoRisolto,
    a: EventoRisolto,
    testo_chunk: str,
) -> QuarantenaItem:
    motivo = "ciclo CAUSA"
    doc_id = ""
    chunk_id = None
    if eventi:
        doc_id = eventi[0].documento or ""
        chunk_id = eventi[0].chunk_id
    segnale = grezzo.segnale_testuale
    span = segnale or da.span or a.span or ""
    text_for_id = testo_chunk or segnale
    return QuarantenaItem(
        id=quarantena_id(doc_id, text_for_id, span, motivo),
        frammento=segnale or f"{da.lemma}->{a.lemma}",
        motivo=motivo,
        ancora_doc=doc_id or None,
        ancora_chunk=chunk_id,
        ancora_span=span or None,
        versione_regole=RULESET_VERSION,
        )


def _iter_menzioni(
    menzioni: dict[str, MenzioneRisolta] | list[MenzioneRisolta] | None,
) -> list[MenzioneRisolta]:
    if not menzioni:
        return []
    if isinstance(menzioni, dict):
        return list(menzioni.values())
    return list(menzioni)


def _mention_forma(item: MenzioneRisolta) -> str:
    return (item.forma_canonica or item.forma or "").strip().casefold()


def _match_argomento(
    forma: str | None,
    menzioni: list[MenzioneRisolta],
) -> str | None:
    needle = (forma or "").strip().casefold()
    if not needle:
        return None
    for item in menzioni:
        hay = _mention_forma(item)
        if hay == needle or needle in hay or hay in needle:
            return item.id
    return None


def _related_finite(
    pred: PredicatoNonFinito,
    eventi: list[EventoRisolto],
    governo: EventoRisolto | None,
    menzioni: list[MenzioneRisolta],
) -> EventoRisolto | None:
    mention_id = _match_argomento(pred.argomento_condiviso, menzioni)
    for event in eventi:
        if governo is not None and event.id == governo.id:
            continue
        if mention_id and any(arg.menzione_id == mention_id for arg in event.argomenti):
            return event
        if pred.argomento_condiviso:
            forma = pred.argomento_condiviso.strip().casefold()
            for arg in event.argomenti:
                if arg.ruolo in ("SOGG", "OGG") and arg.menzione_id:
                    matched = next(
                        (m for m in menzioni if m.id == arg.menzione_id),
                        None,
                    )
                    if matched and forma in _mention_forma(matched):
                        return event
    return None


def _emit_predicati_non_finiti(
    predicati: list[PredicatoNonFinito],
    eventi: list[EventoRisolto],
    menzioni: dict[str, MenzioneRisolta] | list[MenzioneRisolta] | None,
    result: EventEdgesResult,
) -> None:
    """A3 best-effort: typed arc, else MODO/OBL circumstance, else factsheet-only."""
    if not predicati:
        return
    by_indice = _index_by_grezzo(eventi)
    mention_list = _iter_menzioni(menzioni)
    existing = {(arco.da_id, arco.a_id, arco.tipo) for arco in result.archi}
    for pred in predicati:
        governo = by_indice.get(pred.governo_indice) if pred.governo_indice is not None else None
        if governo is None:
            governo = next((event for event in eventi if event.e_testa), None)
        other = _related_finite(pred, eventi, governo, mention_list)
        rel = pred.relazione_segnale
        if governo is not None and other is not None and governo.id != other.id:
            if rel in RELAZIONE_TO_ARCO or rel in COLLEGATO_RELAZIONI:
                mapped = RELAZIONE_TO_ARCO.get(rel)
                tipo: TipoRelazione | str = mapped[0] if mapped else "COLLEGATO"
                key = (governo.id, other.id, tipo)
                if key not in existing:
                    existing.add(key)
                    result.archi.append(
                        ArcoEvento(
                            tipo=tipo,
                            da_id=governo.id,
                            a_id=other.id,
                            props={
                                "regola": REGOLA,
                                "versione_regole": RULESET_VERSION,
                                "segnale": pred.span,
                            },
                        )
                    )
                continue
        if governo is None:
            continue
        mention_id = _match_argomento(pred.argomento_condiviso, mention_list)
        if mention_id is None:
            continue
        if pred.forma_verbale == "gerundio":
            tipo = "MODO"
        else:
            tipo = "OBL"
        key = (governo.id, mention_id, tipo)
        if key in existing:
            continue
        existing.add(key)
        props: dict[str, str] = {
            "regola": REGOLA,
            "versione_regole": RULESET_VERSION,
        }
        if tipo == "OBL":
            props["preposizione"] = "per"
        result.archi.append(
            ArcoEvento(tipo=tipo, da_id=governo.id, a_id=mention_id, props=props)
        )


def _emit_contenuto(
    eventi: list[EventoRisolto],
    by_indice: dict[int, EventoRisolto],
    result: EventEdgesResult,
) -> None:
    existing = {
        (arco.da_id, arco.a_id)
        for arco in result.archi
        if arco.tipo == "CONTENUTO"
    }
    for child in eventi:
        if child.completiva_di is None:
            continue
        parent = by_indice.get(child.completiva_di)
        if parent is None or parent.id == child.id:
            continue
        child.contenuto_di = parent.id
        key = (parent.id, child.id)
        if key in existing:
            continue
        existing.add(key)
        result.archi.append(
            ArcoEvento(
                tipo="CONTENUTO",
                da_id=parent.id,
                a_id=child.id,
                props={
                    "regola": REGOLA,
                    "versione_regole": RULESET_VERSION,
                },
            )
        )


__all__ = [
    "COLLEGATO_RELAZIONI",
    "EventEdgesResult",
    "REGOLA",
    "RELAZIONE_TO_ARCO",
    "categorizza",
    "is_causal_connective",
    "tipo_dopo_anti_causa_inventata",
]
