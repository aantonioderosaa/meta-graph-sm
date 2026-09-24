"""Parte C / MT10 — document-level free relations (Livello 3).

Best-effort extraction only. No Neo4j persistence (that is MT11). Isolation D6:
LLM exclusively via ``infra.llm.call_structured``.
"""

from __future__ import annotations

from typing import Any, get_args

from app.models.event_graph import (
    ArcoEvento,
    EventoRisolto,
    LivelloRelazioniResult,
    RelazioneLibera,
    TipoRelazioneLibera,
)
from app.pipeline.event_graph.event_edges import _causa_reaches
from app.pipeline.event_graph.infra.llm import call_structured
from app.pipeline.event_graph.livello_temporale import (
    LIVELLO_MAX_EVENTI_PER_CHIAMATA,
    _evento_pos_key,
    _span_for,
)

INTESTAZIONE_TESTO = "TESTO"
INTESTAZIONE_EVENTI = "EVENTI (id | frase)"
INTESTAZIONE_TIPI = "RELAZIONI POSSIBILI"
INTESTAZIONE_TABELLA = "TABELLA DELLE RELAZIONI"

SYSTEM_LIVELLO_RELAZIONI = """Ricevi il TESTO del documento, la LISTA degli eventi
(id | frase) e i tipi di relazione ammessi. Non ricevi l'ordine di esposizione.
Non ricostruirlo e non copiarlo come relazione.

Collega due eventi quando la grammatica o il significato del TESTO
marcano un nesso, oppure quando condividono un partecipante, una
situazione o un oggetto su cui quel nesso si appoggia.

Due eventi diversi e vicini NON vanno collegati se non hanno nulla
in comune: la vicinanza da sola non è un nesso.
Se invece hanno qualcosa in comune, il fatto che siano vicini o
consecutivi non è una penalità e non è motivo per omettere la relazione.

Usa ESCLUSIVAMENTE questi tipi:

- CAUSA: un evento provoca, spiega il motivo di, o è la ragione dell'altro.
- CONDIZIONE: un evento accade solo se l'altro si verifica (rapporto
  ipotetico/condizionale fra i due).
- SCOPO: un evento avviene AFFINCHÉ l'altro si realizzi (finalità).
- CONCESSIONE: un evento avviene NONOSTANTE l'altro (contrasto atteso ma
  disatteso).
- CONTRASTO: i due eventi si oppongono o divergono, senza rapporto di
  finalità o concessione.
- LIMITE: un evento vale FINCHÉ l'altro non si verifica (confine temporale
  di validità, non ordine puro).
- CONTENUTO: un evento è ciò che viene detto/pensato/deciso nell'altro
  (l'altro è un atto di dire/pensare/decidere che ha il primo come oggetto).

Non inventare un tipo se manca sia il nesso sia qualsiasi elemento
in comune. La sola successione di eventi senza nulla in comune non
è una relazione. Ogni relazione richiede una spiegazione breve.
Usa solo gli id della lista eventi, mai inventarne.
Italiano o inglese. Temperatura 0."""

_TIPI_LIBERI = frozenset(get_args(TipoRelazioneLibera))
_dropped_causa_ciclo: list[tuple[str, str, str]] = []


def relazioni_causa_ciclo() -> list[tuple[str, str, str]]:
    """(da_id, a_id, tipo) CAUSA triples dropped for acyclicity in the last extract."""
    return list(_dropped_causa_ciclo)


def _visibili(eventi: list[EventoRisolto] | None) -> list[EventoRisolto]:
    return [
        evento
        for evento in list(eventi or [])
        if (evento.id or "").strip() and not evento.fuso_in
    ]


def _evento_line(evento: EventoRisolto) -> str | None:
    eid = (evento.id or "").strip()
    if not eid:
        return None
    return f"{eid} | {_span_for(evento)}"


def _ordine_esposizione(eventi: list[EventoRisolto] | None) -> list[EventoRisolto]:
    """Stable window batches only. Never shown to the model as a sequence."""
    return sorted(_visibili(eventi), key=_evento_pos_key)


def _righe_eventi(eventi: list[EventoRisolto] | None) -> list[str]:
    """Unnumbered id|span lines, sorted by id so list order is not exposition."""
    righe: list[str] = []
    for evento in sorted(_visibili(eventi), key=lambda item: item.id or ""):
        corpo = _evento_line(evento)
        if corpo is None:
            continue
        righe.append(f"- {corpo}")
    return righe


def user_livello_relazioni(
    eventi: list[EventoRisolto],
    *,
    tutti: list[EventoRisolto] | None = None,
    testo: str | None = None,
) -> str:
    """Document text + unnumbered event list + allowed types. No exposition order.

    ``tutti`` is ignored in the prompt (windowing is only a batch of which
    events may be linked). Span fallback: span or lemma|ancora.
    """
    _ = tutti
    corpo_testo = (testo or "").strip() or "(nessun testo)"
    blocco_eventi = "\n".join(_righe_eventi(eventi))
    tipi = "\n".join(f"- {tipo}" for tipo in get_args(TipoRelazioneLibera))
    return (
        f"{INTESTAZIONE_TESTO}\n\n{corpo_testo}\n\n"
        f"{INTESTAZIONE_EVENTI}\n\n{blocco_eventi}\n\n"
        f"{INTESTAZIONE_TIPI}\n\n{tipi}\n\n"
        f"{INTESTAZIONE_TABELLA}\n\n"
        "Compila la tabella delle relazioni di significato. "
        "Usa solo gli id elencati. La lista eventi non è un ordine da copiare. "
        "Vicinanza senza nulla in comune: non collegare. "
        "Vicinanza con qualcosa in comune: non è una penalità."
    )


def _as_relazione(parsed: Any) -> RelazioneLibera | dict[str, Any] | None:
    if isinstance(parsed, RelazioneLibera):
        return parsed
    if isinstance(parsed, dict):
        return parsed
    if parsed is None:
        return None
    try:
        return RelazioneLibera.model_validate(parsed)
    except Exception:
        da = getattr(parsed, "da_id", None)
        a = getattr(parsed, "a_id", None)
        tipo = getattr(parsed, "tipo", None)
        if da is None or a is None or tipo is None:
            return None
        return {
            "da_id": da,
            "a_id": a,
            "tipo": tipo,
            "spiegazione": getattr(parsed, "spiegazione", "") or "",
        }


def _as_result(parsed: Any) -> LivelloRelazioniResult | None:
    if isinstance(parsed, LivelloRelazioniResult):
        return parsed
    raw_list: Any
    if isinstance(parsed, dict):
        raw_list = parsed.get("relazioni")
        if raw_list is None:
            try:
                return LivelloRelazioniResult.model_validate(parsed)
            except Exception:
                return None
    elif hasattr(parsed, "relazioni"):
        raw_list = getattr(parsed, "relazioni")
    else:
        try:
            return LivelloRelazioniResult.model_validate(parsed)
        except Exception:
            return None
    rels: list[RelazioneLibera] = []
    leftovers: list[dict[str, Any]] = []
    for item in raw_list or []:
        candidate = _as_relazione(item)
        if candidate is None:
            continue
        if isinstance(candidate, RelazioneLibera):
            rels.append(candidate)
        else:
            leftovers.append(candidate)
    if leftovers:
        # Keep invalid-tipo rows as constructed objects so sanitize can drop them.
        for raw in leftovers:
            try:
                rels.append(RelazioneLibera.model_construct(
                    da_id=str(raw.get("da_id") or ""),
                    a_id=str(raw.get("a_id") or ""),
                    tipo=raw.get("tipo"),
                    spiegazione="" if raw.get("spiegazione") is None else str(raw.get("spiegazione")),
                ))
            except Exception:
                continue
    return LivelloRelazioniResult(relazioni=rels)


def _known_ids(eventi: list[EventoRisolto]) -> set[str]:
    return {evento.id for evento in eventi if evento.id}


def _fields(rel: RelazioneLibera | dict[str, Any] | Any) -> tuple[str, str, Any, str]:
    if isinstance(rel, dict):
        da = rel.get("da_id") or ""
        a = rel.get("a_id") or ""
        tipo = rel.get("tipo")
        spieg = rel.get("spiegazione")
    else:
        da = getattr(rel, "da_id", "") or ""
        a = getattr(rel, "a_id", "") or ""
        tipo = getattr(rel, "tipo", None)
        spieg = getattr(rel, "spiegazione", "")
    if spieg is None:
        spieg = ""
    return str(da), str(a), tipo, str(spieg)


def _arco_causa(da_id: str, a_id: str) -> ArcoEvento:
    return ArcoEvento(tipo="CAUSA", da_id=da_id, a_id=a_id)


def _sanitize(
    result: LivelloRelazioniResult,
    eventi: list[EventoRisolto],
    archi_causa_esistenti: list[ArcoEvento] | None,
) -> LivelloRelazioniResult:
    global _dropped_causa_ciclo
    _dropped_causa_ciclo = []
    known = _known_ids(eventi)
    causa_kept: list[ArcoEvento] = [
        arco for arco in (archi_causa_esistenti or []) if arco.tipo == "CAUSA"
    ]
    kept: list[RelazioneLibera] = []
    seen: set[tuple[str, str, str]] = set()
    for rel in result.relazioni:
        da_id, a_id, tipo, spiegazione = _fields(rel)
        if da_id not in known or a_id not in known:
            continue
        if da_id == a_id:
            continue
        if tipo not in _TIPI_LIBERI:
            continue
        key = (da_id, a_id, str(tipo))
        if key in seen:
            continue
        if tipo == "CAUSA" and _causa_reaches(causa_kept, a_id, da_id):
            _dropped_causa_ciclo.append((da_id, a_id, "CAUSA"))
            continue
        seen.add(key)
        if tipo == "CAUSA":
            causa_kept.append(_arco_causa(da_id, a_id))
        kept.append(
            RelazioneLibera(
                da_id=da_id,
                a_id=a_id,
                tipo=tipo,
                spiegazione=spiegazione,
            )
        )
    return LivelloRelazioniResult(relazioni=kept)


def _merge_results(parts: list[LivelloRelazioniResult]) -> LivelloRelazioniResult:
    relazioni: list[RelazioneLibera] = []
    for part in parts:
        relazioni.extend(part.relazioni)
    return LivelloRelazioniResult(relazioni=relazioni)


async def _chiama_finestra(
    window: list[EventoRisolto],
    *,
    job_id: str | None,
    testo: str | None,
) -> LivelloRelazioniResult | None:
    try:
        parsed = await call_structured(
            SYSTEM_LIVELLO_RELAZIONI,
            user_livello_relazioni(window, testo=testo),
            LivelloRelazioniResult,
            temperature=0,
            job_id=job_id,
        )
    except Exception:
        return None
    return _as_result(parsed)


async def estrai_livello_relazioni(
    eventi: list[EventoRisolto],
    *,
    job_id: str | None = None,
    testo: str | None = None,
    archi_causa_esistenti: list[ArcoEvento] | None = None,
) -> LivelloRelazioniResult | None:
    """Best-effort, windows of LIVELLO_MAX_EVENTI_PER_CHIAMATA if needed.

    Every LLM call receives the document text, an unnumbered event list, and
    the allowed relation types — not exposition order. Merge relazioni,
    dedupe (da_id, a_id, tipo), then sanitize. LLM failure on a window
    contributes nothing; if every window fails, return None. Never raises.
    """
    try:
        items = _visibili(list(eventi or []))
    except Exception:
        return None
    if not items:
        return LivelloRelazioniResult()
    try:
        ordered = _ordine_esposizione(items)
        cap = LIVELLO_MAX_EVENTI_PER_CHIAMATA
        windows = [ordered[i : i + cap] for i in range(0, len(ordered), cap)]
        parts: list[LivelloRelazioniResult] = []
        for window in windows:
            part = await _chiama_finestra(
                window,
                job_id=job_id,
                testo=testo,
            )
            if part is None:
                continue
            parts.append(part)
        if not parts:
            return None
        merged = _merge_results(parts)
        return _sanitize(merged, items, archi_causa_esistenti)
    except Exception:
        return None


__all__ = [
    "INTESTAZIONE_EVENTI",
    "INTESTAZIONE_TABELLA",
    "INTESTAZIONE_TESTO",
    "INTESTAZIONE_TIPI",
    "SYSTEM_LIVELLO_RELAZIONI",
    "estrai_livello_relazioni",
    "relazioni_causa_ciclo",
    "user_livello_relazioni",
]
