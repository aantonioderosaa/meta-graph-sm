"""MICRO stages 0–2 — per-zone event extraction, then dedup (Addendum 4).

Extraction now runs ``extraction_simple.extract_event_entities`` **once per
zone** ("chunk") instead of the retired per-sentence grammatical extraction:
one independent-clause event + its participant entities, no further parse
(tempo/segmentazione/SOGG-OGG roles get neutral defaults below). Dedup
(mention/event coref) is unchanged and always runs before inter-sentence
linking (stages 3/4), which this module does not call.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.models.event_graph import (
    ArgomentoRisolto,
    EventEntityExtractionResult,
    EventEntityParticipation,
    EventoRisolto,
    MenzioneRisolta,
    PredicatoNonFinito,
    SottoGrafo,
)
from app.pipeline.event_graph import RULESET_VERSION, chains, event_coref, factuality, mention_coref
from app.pipeline.event_graph.chunking_periods import UnitaTesto, connettivo_confine
from app.pipeline.event_graph.event_coref import EsitoCoref
from app.pipeline.event_graph.extraction_simple import extract_event_entities
from app.pipeline.event_graph.ids import evento_id, menzione_id
from app.pipeline.event_graph.segmentation import SegmentationResult

STAGE_ORDER = ("preprocess", "extract", "dedup", "pair", "nonadj", "allen")
STAGES_FINO_DEDUP = STAGE_ORDER[: STAGE_ORDER.index("dedup") + 1]

REGOLA = "extraction_simple.extract_event_entities"

EstraiEventi = Callable[..., Awaitable[EventEntityExtractionResult]]

_PROPER_START = re.compile(r"^[A-ZÀ-Þ]")


@dataclass
class DedupResult:
    sotto: SottoGrafo
    esiti: list[EsitoCoref] = field(default_factory=list)
    unita: list[UnitaTesto] = field(default_factory=list)
    predicati_non_finiti: list[PredicatoNonFinito] = field(default_factory=list)


def _pos_key(evento: EventoRisolto) -> tuple[int, int, str]:
    return (
        evento.posizione_doc if evento.posizione_doc is not None else 0,
        evento.posizione_chunk if evento.posizione_chunk is not None else 0,
        evento.id or "",
    )


def _find_offset(haystack: str, needle: str, start_from: int) -> tuple[int, int]:
    """Locate ``needle`` at or after ``start_from``; monotonic fallback if absent."""
    if needle:
        idx = haystack.find(needle, start_from)
        if idx < 0:
            idx = haystack.find(needle)
        if idx >= 0:
            return idx, idx + len(needle)
    return start_from, start_from + max(len(needle), 1)


def _tipo_superficiale(nome: str) -> str:
    """Cheap heuristic: capitalized → nome_proprio, else sn_comune.

    Does not change identity (``ids.menzione_id`` hashes both the same way
    on the normalised form) — only the persisted surface-type label.
    """
    return "nome_proprio" if _PROPER_START.match(nome or "") else "sn_comune"


def _dedup_entities(entities: Sequence[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in entities or []:
        nome = (raw or "").strip()
        if not nome or nome.casefold() in seen:
            continue
        seen.add(nome.casefold())
        out.append(nome)
    return out


def _evento_da_partecipazione(
    part: EventEntityParticipation,
    unita: UnitaTesto,
    *,
    doc_id: str,
    zona_testo: str,
    ordinale: int,
    indice: int,
) -> tuple[EventoRisolto, list[MenzioneRisolta]]:
    evento_testo = unita.testo
    entita = _dedup_entities(part.entities)

    menzioni: list[MenzioneRisolta] = []
    argomenti: list[ArgomentoRisolto] = []
    for pos, nome in enumerate(entita):
        tipo = _tipo_superficiale(nome)
        minted = menzione_id(nome, tipo, doc_id, unita.zona_id or "", pos)
        menzioni.append(
            MenzioneRisolta(
                id=minted.id,
                forma=nome,
                forma_canonica=nome,
                tipo_superficiale=tipo,  # type: ignore[arg-type]
                non_risolto=minted.non_risolto,
                documento=doc_id,
                chunk_id=unita.zona_id,
                regola=REGOLA,
                versione_regole=RULESET_VERSION,
            )
        )
        argomenti.append(
            ArgomentoRisolto(
                ruolo="SOGG" if pos == 0 else "OGG",
                menzione_id=minted.id,
            )
        )

    evento = EventoRisolto(
        id=evento_id(doc_id, zona_testo, indice),
        lemma=evento_testo,
        segmentazione="principale_finita",
        e_testa=True,
        frase_tipo="dichiarativa",
        frase_indice=indice,
        documento=doc_id,
        chunk_id=unita.zona_id,
        indice_chunk=indice,
        posizione_doc=ordinale,
        posizione_chunk=indice,
        offset_inizio=unita.offset_inizio,
        offset_fine=unita.offset_fine,
        ancora=evento_testo,
        span=evento_testo,
        sogg_speciale="nessuno" if entita else "IGNOTO",
        argomenti=argomenti,
        regola=REGOLA,
        versione_regole=RULESET_VERSION,
    )
    return evento, menzioni


async def espandi_zona_fino_dedup(
    zona: object,
    *,
    document_text: str | None = None,
    job_id: str | None = None,
    session: Any = None,
    estrai_frase: EstraiEventi | None = None,
) -> DedupResult:
    """Stages 0–2 (Addendum 4).

    1. ``extract_event_entities(zona.testo)`` — one call, replaces the whole
       per-sentence grammatical extraction.
    2. one ``EventoRisolto`` per participation (neutral grammar defaults;
       argomenti minted from ``participation.entities``) + one ``UnitaTesto``
       per event, so ``sentence_pair_linking``'s head/adjacency lookup keeps
       working unmodified.
    3. factuality.applica / mention_coref.risolvi_intra / event_coref+chains,
       same sequential loop as before.

    ``document_text`` is unused here (kept for call-site compatibility);
    ``estrai_frase`` — if given — replaces ``extract_event_entities`` (same
    ``(chunk_text, job_id=...)`` signature), for tests/injection.
    """
    del document_text
    extract = estrai_frase if estrai_frase is not None else extract_event_entities
    sotto = SottoGrafo()
    esiti: list[EsitoCoref] = []

    zona_testo = getattr(zona, "testo", None) or ""
    if not zona_testo.strip():
        return DedupResult(sotto=sotto, esiti=esiti, unita=[], predicati_non_finiti=[])

    zona_id = getattr(zona, "id", None)
    zona_id_str = str(zona_id) if zona_id is not None else None
    doc_id = getattr(zona, "documento", None) or ""
    base_offset = int(getattr(zona, "offset_inizio", 0) or 0)
    ordinale = int(getattr(zona, "ordinale", 0) or 0)

    try:
        result = await extract(zona_testo, job_id=job_id)
    except Exception:
        return DedupResult(sotto=sotto, esiti=esiti, unita=[], predicati_non_finiti=[])

    units: list[UnitaTesto] = []
    cursor = 0
    previous_testo: str | None = None

    for indice, part in enumerate(result.participations or []):
        evento_testo = (part.event or "").strip()
        if not evento_testo:
            continue
        start, end = _find_offset(zona_testo, evento_testo, cursor)
        cursor = max(cursor, end)
        unita = UnitaTesto(
            testo=evento_testo,
            offset_inizio=base_offset + start,
            offset_fine=base_offset + end,
            tipo="narrativa",
            connettivo_confine=connettivo_confine(evento_testo, previous_testo),
            zona_id=zona_id_str,
            indice=indice,
        )
        previous_testo = evento_testo
        units.append(unita)

        evento, menzioni = _evento_da_partecipazione(
            part,
            unita,
            doc_id=doc_id,
            zona_testo=zona_testo,
            ordinale=ordinale,
            indice=indice,
        )

        factuality.applica([evento])
        seg = SegmentationResult(eventi=[evento], menzioni=menzioni, quarantena=[])
        mention_coref.risolvi_intra(seg, None, sotto)

        sotto.aggiungi(eventi=[evento], menzioni=menzioni)

        pool = [item for item in sotto.eventi if item.id != evento.id]
        found = event_coref.candidati(evento, pool)
        esito = event_coref.classifica(evento, found, sotto=sotto)
        if esito is not None:
            esiti.append(esito)
        chains.applica(sotto, evento, esito)

    return DedupResult(
        sotto=sotto,
        esiti=esiti,
        unita=units,
        predicati_non_finiti=[],
    )


__all__ = [
    "STAGE_ORDER",
    "STAGES_FINO_DEDUP",
    "DedupResult",
    "espandi_zona_fino_dedup",
]
