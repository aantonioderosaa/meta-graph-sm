"""MACRO + MICRO event-graph ingestion orchestrator (Addendum 2 M-flash)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from app.models.event_graph import ArcoEvento, EventoRisolto, SottoGrafo
from app.pipeline.event_graph import (
    RULESET_VERSION,
    chains,
    event_coref,
    mention_coref,
    persistence,
)
from app.pipeline.event_graph.ancore_estrazione import estrai_ancore
from app.pipeline.event_graph.ancore_identita import esegui_ancore_identita
from app.pipeline.event_graph.ancore_linea import esegui_ancore_linea
from app.pipeline.event_graph.ancore_smistamento import (
    SmistamentoAncore,
    smista_eventi,
)
from app.pipeline.event_graph.buchi import riempi_buchi_documento
from app.pipeline.event_graph.chiusura_temporale import chiusura_temporale
from app.pipeline.event_graph.chunking_periods import UnitaTesto, connettivo_confine
from app.pipeline.event_graph.config import settings
from app.pipeline.event_graph.dedup import DedupResult, espandi_zona_fino_dedup
from app.pipeline.event_graph.evinti import evinci_eventi_documento
from app.pipeline.event_graph.ids import content_hash
from app.pipeline.event_graph.infra.bus import publish
from app.pipeline.event_graph.infra.driver import get_driver, get_session
from app.pipeline.event_graph.infra.schema_bootstrap import ensure_event_graph_schema
from app.pipeline.event_graph.livello_relazioni import estrai_livello_relazioni
from app.pipeline.event_graph.ponte_verifica import verifica_dopo_espansione
from app.pipeline.event_graph.sentence_pair_linking import collega_inter_frase
from app.pipeline.event_graph.via_principale import annota_vie, calcola_vie
from app.pipeline.event_graph.zona_edges import ArcoZona, collega_zone
from app.pipeline.event_graph.zona_segmentation import Zona, segmenta_zone
from app.pipeline.event_graph.zona_summary import riassumi_zone
from app.pipeline.event_graph.zona_transizioni import genera_transizioni_zona

# Regola/base of the intra-zone exposition dorsale (collega_dorsale_eventi).
REGOLA_DORSALE_ESPOSIZIONE = "pipeline.dorsale_esposizione"
BASE_DORSALE_ESPOSIZIONE = "esposizione"


@dataclass
class IngestionOutcome:
    sotto: SottoGrafo
    llm_calls: int
    chunks_kept: int
    chunks_skipped: int
    reused_factsheets: int
    job_id: str
    doc_id: str
    zone: list[Zona] = field(default_factory=list)
    archi_macro: list[ArcoZona] = field(default_factory=list)
    vie: dict = field(default_factory=dict)
    zones_expanded: int = 0


@dataclass
class EspansioneZona:
    zona: Zona
    sotto: SottoGrafo
    unita: list


def zone_da_espandere_subito(zone, vie, flash_mode: bool) -> list[Zona]:
    """Eager MICRO set: all zones, or the union of the three main paths."""
    if not flash_mode:
        return list(zone)
    union_ids: set[str] = set()
    for via in (vie or {}).values():
        ids = getattr(via, "zona_ids", None)
        if ids is None and isinstance(via, dict):
            ids = via.get("zona_ids")
        union_ids.update(ids or [])
    return [item for item in zone if item.id in union_ids]


def _merge_sotto(sotto_doc: SottoGrafo, piece: SottoGrafo) -> None:
    sotto_doc.aggiungi(
        eventi=list(piece.eventi),
        archi=list(piece.archi),
        quarantena=list(piece.quarantena),
        menzioni=list(piece.menzioni.values()),
    )


def _eventi_di_zona(sotto: SottoGrafo, zona_id: str) -> list[EventoRisolto]:
    """Events of one zone, in appearance order, skipping fused duplicates."""
    return sorted(
        (e for e in sotto.eventi if e.chunk_id == zona_id and not e.fuso_in),
        key=lambda e: e.posizione_chunk if e.posizione_chunk is not None else 0,
    )


def _ponte_gia_presente(sotto: SottoGrafo, da_id: str, a_id: str) -> bool:
    return any(
        arco.tipo == "SEQUENZA" and arco.da_id == da_id and arco.a_id == a_id
        for arco in sotto.archi
    )


def collega_dorsale_zone(sotto: SottoGrafo, zone: list[Zona]) -> None:
    """The narrative railway: one SEQUENZA arc from the last event of each
    expanded zone to the first event of the next expanded one, in exposition
    order (zona.ordinale). Non-invasive by construction: runs once, after all
    per-zone linking (sentence_pair_linking / chiusura_temporale) is done,
    and only ever appends arcs — never touches those modules or re-classifies
    anything. A zone left unexpanded (flash mode, not yet requested) is
    skipped, bridging straight to the next expanded one.
    """
    ordinate = sorted((z for z in zone if z.espansa), key=lambda z: z.ordinale)
    precedente: EventoRisolto | None = None
    for zona in ordinate:
        eventi_zona = _eventi_di_zona(sotto, zona.id)
        if not eventi_zona:
            continue
        primo, ultimo = eventi_zona[0], eventi_zona[-1]
        if precedente is not None and precedente.id != primo.id:
            if not _ponte_gia_presente(sotto, precedente.id, primo.id):
                sotto.archi.append(
                    ArcoEvento(
                        tipo="SEQUENZA",
                        da_id=precedente.id,
                        a_id=primo.id,
                        props={
                            "regola": "pipeline.collega_dorsale_zone",
                            "versione_regole": RULESET_VERSION,
                            "livello": "ponte_zona",
                        },
                    )
                )
        precedente = ultimo


def _pos_key_esposizione(evento: EventoRisolto) -> tuple[int, int, int, str]:
    """Exposition order inside one zone: posizione_chunk first, then fallbacks."""
    return (
        evento.posizione_chunk if evento.posizione_chunk is not None else 0,
        evento.posizione_doc if evento.posizione_doc is not None else 0,
        evento.offset_inizio if evento.offset_inizio is not None else 0,
        evento.id or "",
    )


def collega_dorsale_eventi(sotto: SottoGrafo, zone: list[Zona]) -> None:
    """The intra-zone half of the narrative railway: one SEQUENZA arc between
    every pair of consecutive events of the same zone, in exposition order.

    Deterministic by construction (``posizione_chunk`` is already known, no LLM
    call, no re-classification): the blue chain must not depend on which label
    the pair classifier happened to pick. Append-only and idempotent — an arc is
    written only when that ordered pair has no SEQUENZA yet, and the content-hash
    ``id`` makes the Neo4j MERGE stable across runs. A pre-existing CAUSA on
    the same pair is left alone and the dorsale arc is added next to it:
    exposition order and semantic relation are two distinct assertions.

    Cross-zone joints are ``collega_dorsale_zone``'s job, not this one's.
    """
    for zona in sorted(zone, key=lambda z: z.ordinale):
        eventi_zona = sorted(
            (
                event
                for event in sotto.eventi
                if event.chunk_id == zona.id and event.id and not event.fuso_in
            ),
            key=_pos_key_esposizione,
        )
        for precedente, successivo in zip(eventi_zona, eventi_zona[1:]):
            if precedente.id == successivo.id:
                continue
            if _ponte_gia_presente(sotto, precedente.id, successivo.id):
                continue
            sotto.archi.append(
                ArcoEvento(
                    tipo="SEQUENZA",
                    da_id=precedente.id,
                    a_id=successivo.id,
                    props={
                        "id": content_hash(
                            f"SEQUENZA|{precedente.id}|{successivo.id}"
                            f"|{BASE_DORSALE_ESPOSIZIONE}"
                        ),
                        "base": BASE_DORSALE_ESPOSIZIONE,
                        "regola": REGOLA_DORSALE_ESPOSIZIONE,
                        "versione_regole": RULESET_VERSION,
                    },
                )
            )


@asynccontextmanager
async def _maybe_session(session) -> AsyncIterator:
    """Use the given session, or a short-lived driver session. Never nest concurrent runs."""
    if session is not None:
        yield session
        return
    try:
        async with get_session() as owned:
            yield owned
    except RuntimeError:
        yield None


def _unita_da_eventi_zona(sotto: SottoGrafo, zona: Zona) -> list[UnitaTesto]:
    """One UnitaTesto per visible event, in exposition order (includes buchi)."""
    eventi = [
        event
        for event in sotto.eventi
        if event.chunk_id == zona.id and event.id and not event.fuso_in
    ]
    eventi.sort(key=_pos_key_esposizione)
    units: list[UnitaTesto] = []
    previous: str | None = None
    for index, evento in enumerate(eventi):
        testo = (evento.span or evento.ancora or evento.lemma or "").strip()
        start = evento.offset_inizio if evento.offset_inizio is not None else 0
        end = (
            evento.offset_fine
            if evento.offset_fine is not None
            else start + max(len(testo), 1)
        )
        indice = (
            evento.posizione_chunk if evento.posizione_chunk is not None else index
        )
        units.append(
            UnitaTesto(
                testo=testo,
                offset_inizio=start,
                offset_fine=end,
                tipo="narrativa",
                connettivo_confine=connettivo_confine(testo, previous),
                zona_id=zona.id,
                indice=indice,
            )
        )
        previous = testo
    return units


def _sotto_della_zona(sotto: SottoGrafo, zona: Zona) -> SottoGrafo:
    ids = {event.id for event in sotto.eventi if event.chunk_id == zona.id and event.id}
    piece = SottoGrafo()
    piece.aggiungi(
        eventi=[event for event in sotto.eventi if event.chunk_id == zona.id],
        archi=[
            arco
            for arco in sotto.archi
            if arco.da_id in ids and arco.a_id in ids
        ],
        menzioni=list(sotto.menzioni.values()),
    )
    return piece


def _assorbi_relazioni(sotto_doc: SottoGrafo, piece: SottoGrafo) -> None:
    seen = {(arco.da_id, arco.a_id, arco.tipo) for arco in sotto_doc.archi}
    for arco in piece.archi:
        key = (arco.da_id, arco.a_id, arco.tipo)
        if key in seen:
            continue
        sotto_doc.archi.append(arco)
        seen.add(key)
    known = {item.id for item in sotto_doc.quarantena if item.id}
    for item in piece.quarantena:
        if item.id and item.id in known:
            continue
        sotto_doc.quarantena.append(item)
        if item.id:
            known.add(item.id)


async def estrai_zona(
    zona: Zona,
    *,
    document_text: str | None = None,
    zone: list[Zona],
    archi_macro: list[ArcoZona],
    sotto_doc: SottoGrafo,
    job_id=None,
    session=None,
    estrai_frase=None,
) -> EspansioneZona:
    """Stages 0–2 only: extract + dedup. No pair linking, no Allen."""
    del zone, archi_macro
    if job_id:
        await publish(
            job_id,
            "espansione",
            "zona_start",
            {"zona_id": zona.id, "ordinale": zona.ordinale},
        )
    dedup: DedupResult = await espandi_zona_fino_dedup(
        zona,
        document_text=document_text,
        job_id=job_id,
        session=session,
        estrai_frase=estrai_frase,
    )
    _merge_sotto(sotto_doc, dedup.sotto)
    zona.espansa = True
    async with _maybe_session(session) as persist_session:
        if persist_session is not None:
            await mention_coref.fondi_referenziali_vs_persistente(
                persist_session, dedup.sotto
            )
            await persistence.persisti_zona(persist_session, zona)
            await persistence.persisti(persist_session, dedup.sotto, job_id=job_id)
    if job_id:
        n_eventi = sum(
            1
            for event in dedup.sotto.eventi
            if event.chunk_id == zona.id and not event.fuso_in
        )
        await publish(
            job_id,
            "espansione",
            "zona_extracted",
            {"zona_id": zona.id, "eventi": n_eventi},
        )
    return EspansioneZona(zona=zona, sotto=dedup.sotto, unita=list(dedup.unita))


async def collega_relazioni_zona(
    zona: Zona,
    sotto_doc: SottoGrafo,
    *,
    zone: list[Zona],
    archi_macro: list[ArcoZona],
    job_id=None,
    session=None,
) -> None:
    """Adjacent MICRO linking on the zona including buchi, then ponte + persist."""
    unita = _unita_da_eventi_zona(sotto_doc, zona)
    piece = _sotto_della_zona(sotto_doc, zona)
    dedup = DedupResult(sotto=piece, unita=unita)
    await collega_inter_frase(dedup, job_id=job_id)
    chiusura_temporale(piece, unita)
    _assorbi_relazioni(sotto_doc, piece)
    verifica_dopo_espansione(zona.id, zone, archi_macro, sotto_doc)
    async with _maybe_session(session) as persist_session:
        if persist_session is not None:
            await persistence.persisti(persist_session, sotto_doc, job_id=job_id)
            await persistence.persisti_archi_macro(persist_session, archi_macro)
    if job_id:
        n_eventi = sum(
            1
            for event in sotto_doc.eventi
            if event.chunk_id == zona.id and not event.fuso_in
        )
        await publish(
            job_id,
            "espansione",
            "zona_expanded",
            {"zona_id": zona.id, "eventi": n_eventi},
        )


async def espandi_zona(
    zona: Zona,
    *,
    document_text: str | None = None,
    zone: list[Zona],
    archi_macro: list[ArcoZona],
    sotto_doc: SottoGrafo,
    job_id=None,
    session=None,
    estrai_frase=None,
) -> EspansioneZona:
    """Single-zona expand (API): extract then MICRO relations. Ingest splits them."""
    result = await estrai_zona(
        zona,
        document_text=document_text,
        zone=zone,
        archi_macro=archi_macro,
        sotto_doc=sotto_doc,
        job_id=job_id,
        session=session,
        estrai_frase=estrai_frase,
    )
    await collega_relazioni_zona(
        zona,
        sotto_doc,
        zone=zone,
        archi_macro=archi_macro,
        job_id=job_id,
        session=session,
    )
    return result


async def _ensure_schema(driver=None) -> None:
    resolved = driver
    if resolved is None:
        try:
            resolved = get_driver()
        except RuntimeError:
            return
    try:
        await ensure_event_graph_schema(resolved)
    except RuntimeError:
        return


async def _maybe_registra_run(
    session,
    job_id: str,
    doc_id: str,
    *,
    sotto: SottoGrafo | None = None,
    temporal=None,
    extra=None,
) -> None:
    try:
        from app.pipeline.event_graph.metrics import registra_run
    except ImportError:
        return
    await registra_run(
        session,
        job_id,
        doc_id,
        RULESET_VERSION,
        sotto=sotto,
        temporal=temporal,
        extra=extra,
    )


async def esegui_livello_ancore(
    zone: list[Zona],
    sotto: SottoGrafo,
    doc_id: str,
    job_id: str,
    session=None,
    *,
    call_structured=None,
) -> SmistamentoAncore:
    """Sole temporal path: TEMPO mentions → identity → line → assign → persist.

    Failures propagate (SSE already published by each module). Does not import
    or call ``livello_temporale`` / ``temporal_placement``. Does not derive
    event-to-event PRECEDE / CONTEMPORANEO. Dates are not re-extracted here.
    """
    estrazione = await estrai_ancore(
        zone,
        job_id=job_id,
        call_structured=call_structured,
        eventi=sotto.eventi,
        menzioni=sotto.menzioni,
    )
    ancore = await esegui_ancore_identita(
        estrazione.livello, documento=doc_id, job_id=job_id
    )
    linea = await esegui_ancore_linea(ancore, documento=doc_id, job_id=job_id)
    smistamento = await smista_eventi(
        linea,
        sotto.eventi,
        zone=zone,
        menzioni=sotto.menzioni,
        documento=doc_id,
        job_id=job_id,
        call_structured=call_structured,
    )
    async with _maybe_session(session) as persist_session:
        if persist_session is not None:
            await persistence.persisti_livello_ancore(
                persist_session,
                smistamento,
                doc_id,
                job_id,
                eventi=sotto.eventi,
            )
    return smistamento


async def _run_fase_b(session, sotto: SottoGrafo, job_id: str, doc_id: str) -> None:
    await publish(job_id, "riconciliazione", "reconcile_start", {"doc_id": doc_id})
    mention_coref.risolvi_intra(sotto.eventi, None, sotto)
    await mention_coref.fondi_referenziali_vs_persistente(session, sotto)
    await persistence.persisti(session, sotto, job_id=job_id)
    # collega_dorsale_eventi/zone (già in sotto.archi sopra) possono coprire
    # una coppia il cui COLLEGATO era già stato scritto nel MERGE per-zona,
    # prima che la dorsale esistesse: quel COLLEGATO non viene mai rivisto
    # dal dedup di persisti() perché non è più nel batch. Un solo giro qui,
    # con tutte le SEQUENZA del documento ormai a grafo: marca (mai cancella,
    # append-only) i COLLEGATO ormai ridondanti.
    await persistence.sopprimi_collegato_ridondanti(session, doc_id)
    for event in sotto.eventi_per_posizione():
        esito = event_coref.classifica(
            event,
            await event_coref.persistente_candidati(session, event),
        )
        await chains.applica_persistente(session, event, esito)
    await publish(job_id, "riconciliazione", "reconcile_done", {"doc_id": doc_id})
    await _maybe_registra_run(session, job_id, doc_id, sotto=sotto, temporal=None)


async def _fase_b_if_available(
    session,
    sotto: SottoGrafo,
    job_id: str,
    doc_id: str,
) -> None:
    if session is not None:
        await _run_fase_b(session, sotto, job_id, doc_id)
        return
    try:
        async with get_session() as owned:
            await _run_fase_b(owned, sotto, job_id, doc_id)
    except RuntimeError:
        return


async def _persist_macro(
    session,
    doc_id: str,
    text: str,
    zone: list[Zona],
    archi_macro: list[ArcoZona],
) -> None:
    async with _maybe_session(session) as persist_session:
        if persist_session is None:
            return
        await persistence.persisti_documento(persist_session, doc_id, testo=text)
        await persistence.persisti_zone(persist_session, zone)
        await persistence.persisti_archi_macro(persist_session, archi_macro)


async def run_event_graph_ingestion(
    doc_id: str,
    text: str,
    job_id: str,
    *,
    session=None,
    driver=None,
) -> IngestionOutcome:
    """MACRO (M0-M3) then MICRO expansion according to FLASH_MODE."""
    try:
        await publish(job_id, "macro", "macro_start", {"doc_id": doc_id})
        await _ensure_schema(driver)
        sotto = SottoGrafo()
        llm_calls = 0
        reused_factsheets = 0

        zone = segmenta_zone(text, doc_id)
        zone = await riassumi_zone(zone, job_id=job_id, session=session)
        archi_macro = await collega_zone(zone, job_id=job_id)
        vie = calcola_vie(zone, archi_macro)
        zone, archi_macro = annota_vie(zone, archi_macro, vie)
        await _persist_macro(session, doc_id, text, zone, archi_macro)
        await publish(
            job_id,
            "macro",
            "macro_done",
            {"doc_id": doc_id, "n_zone": len(zone), "n_archi": len(archi_macro)},
        )

        chosen = zone_da_espandere_subito(
            zone, vie, settings.EVENT_GRAPH_FLASH_MODE
        )
        for item in chosen:
            await estrai_zona(
                item,
                document_text=text,
                zone=zone,
                archi_macro=archi_macro,
                sotto_doc=sotto,
                job_id=job_id,
                session=session,
            )

        zones_expanded = sum(1 for item in zone if item.espansa)
        chunks_kept = zones_expanded
        chunks_skipped = max(len(zone) - zones_expanded, 0)

        # Holes before MICRO zone relations so leftover sentences are linked.
        try:
            aggiunti_buchi = await riempi_buchi_documento(
                sotto, zone, job_id=job_id, session=session
            )
        except Exception:
            aggiunti_buchi = []
        if aggiunti_buchi:
            try:
                async with _maybe_session(session) as persist_session:
                    if persist_session is not None:
                        await persistence.persisti(
                            persist_session, sotto, job_id=job_id
                        )
            except Exception:
                pass
            if job_id:
                await publish(
                    job_id,
                    "espansione",
                    "buchi_done",
                    {"n": len(aggiunti_buchi)},
                )

        for item in chosen:
            if not item.espansa:
                continue
            await collega_relazioni_zona(
                item,
                sotto,
                zone=zone,
                archi_macro=archi_macro,
                job_id=job_id,
                session=session,
            )

        collega_dorsale_eventi(sotto, zone)
        collega_dorsale_zone(sotto, zone)
        try:
            aggiunti_evinti = await evinci_eventi_documento(
                sotto, zone, job_id=job_id, session=session
            )
        except Exception:
            aggiunti_evinti = []
        if aggiunti_evinti:
            try:
                async with _maybe_session(session) as persist_session:
                    if persist_session is not None:
                        await persistence.persisti(
                            persist_session, sotto, job_id=job_id
                        )
            except Exception:
                pass
        try:
            mancanti = [
                item
                for item in zone
                if item.espansa and not (item.riassunto or "").strip()
            ]
            if mancanti:
                recuperate = await riassumi_zone(
                    mancanti, job_id=job_id, session=session
                )
                by_id = {item.id: item for item in recuperate}
                zone = [by_id.get(item.id, item) for item in zone]
                async with _maybe_session(session) as persist_session:
                    if persist_session is not None:
                        await persistence.persisti_zone(persist_session, zone)
        except Exception:
            pass
        try:
            transizioni = await genera_transizioni_zona(zone, job_id=job_id)
        except Exception:
            transizioni = {}
        try:
            async with _maybe_session(session) as persist_session:
                if persist_session is not None:
                    await persistence.persisti_transizioni_zona(
                        persist_session, transizioni, job_id
                    )
        except Exception:
            pass
        if settings.EVENT_GRAPH_TEMPORAL_ENABLED:
            await esegui_livello_ancore(zone, sotto, doc_id, job_id, session)
        # Flag false: no LLM temporal, no AncoraTemporale writes, no
        # temporal_placement, no ClusterTemporale path (dead, not re-enabled).
        await publish(
            job_id,
            "relazioni",
            "relazioni_start",
            {"n_eventi": len(sotto.eventi)},
        )
        try:
            livello_rel = await estrai_livello_relazioni(
                sotto.eventi,
                testo=text,
                job_id=job_id,
                archi_causa_esistenti=sotto.archi,
            )
        except Exception:
            livello_rel = None
        try:
            async with _maybe_session(session) as persist_session:
                if persist_session is not None:
                    await persistence.persisti_livello_relazioni(
                        persist_session, livello_rel, job_id, eventi=sotto.eventi
                    )
        except Exception:
            pass
        await publish(
            job_id,
            "relazioni",
            "relazioni_done",
            {"ok": livello_rel is not None},
        )

        await _fase_b_if_available(session, sotto, job_id, doc_id)
        stats = {
            "doc_id": doc_id,
            "chunks_kept": chunks_kept,
            "chunks_skipped": chunks_skipped,
            "zones_expanded": zones_expanded,
            "llm_calls": llm_calls,
            "reused_factsheets": reused_factsheets,
            "eventi": len(sotto.eventi),
        }
        await publish(job_id, "done", "pipeline_complete", stats)
        return IngestionOutcome(
            sotto=sotto,
            llm_calls=llm_calls,
            chunks_kept=chunks_kept,
            chunks_skipped=chunks_skipped,
            reused_factsheets=reused_factsheets,
            job_id=job_id,
            doc_id=doc_id,
            zone=zone,
            archi_macro=archi_macro,
            vie=vie,
            zones_expanded=zones_expanded,
        )
    except Exception as exc:
        await publish(
            job_id,
            "failed",
            "pipeline_failed",
            {"error": str(exc)},
        )
        raise


__all__ = [
    "BASE_DORSALE_ESPOSIZIONE",
    "EspansioneZona",
    "IngestionOutcome",
    "REGOLA_DORSALE_ESPOSIZIONE",
    "collega_dorsale_eventi",
    "collega_dorsale_zone",
    "collega_relazioni_zona",
    "esegui_livello_ancore",
    "espandi_zona",
    "estrai_zona",
    "evinci_eventi_documento",
    "riempi_buchi_documento",
    "run_event_graph_ingestion",
    "zone_da_espandere_subito",
]
