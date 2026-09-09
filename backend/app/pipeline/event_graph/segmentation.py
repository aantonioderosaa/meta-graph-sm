"""§3 — ChunkFactsheet → EventoRisolto / MenzioneRisolta (piano sez. 9).

``posizione_chunk`` is the order of first span appearance in ``chunk.testo``
(case-sensitive ``str.find``). Missing spans are appended in factsheet order.
Ids use that appearance order, not the LLM ``indice``.

Events listed on ``factsheet.quarantena`` or lacking a span become
``QuarantenaItem``s (returned in ``SegmentationResult.quarantena``) rather
than ``EventoRisolto``. Mention ids are minted only — no §9 coref fusion (M6).
``fattualita``, ``piano`` and ``fonte`` stay None (M4).
``indice_grezzo`` copies the LLM ``indice`` so M4 can resolve ``completiva_di``
after span-reordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.event_graph import (
    ArgomentoRisolto,
    ChunkFactsheet,
    EventoGrezzo,
    EventoRisolto,
    FraseFactsheet,
    MenzioneRisolta,
    PredicatoNonFinito,
    QuarantenaItem,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.chunking_periods import PeriodChunk, UnitaTesto
from app.pipeline.event_graph.extraction import frammento_to_item
from app.pipeline.event_graph.ids import evento_id, menzione_id, quarantena_id
from app.pipeline.event_graph.text_norm import fold_text

_REGOLA = "segmentation.risolvi"


@dataclass
class SegmentationResult:
    """Resolved events, minted mentions, and quarantena produced by ``risolvi``."""

    eventi: list[EventoRisolto] = field(default_factory=list)
    menzioni: list[MenzioneRisolta] = field(default_factory=list)
    quarantena: list[QuarantenaItem] = field(default_factory=list)
    predicati_non_finiti: list[PredicatoNonFinito] = field(default_factory=list)


def _has_span(event: EventoGrezzo) -> bool:
    return bool(event.span and event.span.strip())


def _order_events(eventi: list[EventoGrezzo], testo: str) -> list[EventoGrezzo]:
    found: list[tuple[int, int, EventoGrezzo]] = []
    missing: list[tuple[int, EventoGrezzo]] = []
    folded_testo = fold_text(testo)
    for factsheet_i, event in enumerate(eventi):
        if _has_span(event):
            start = folded_testo.find(fold_text(event.span))
            if start >= 0:
                found.append((start, factsheet_i, event))
                continue
        missing.append((factsheet_i, event))
    found.sort(key=lambda item: (item[0], item[1]))
    missing.sort(key=lambda item: item[0])
    return [item[2] for item in found] + [item[1] for item in missing]


def _span_quarantena(event: EventoGrezzo, chunk: PeriodChunk) -> QuarantenaItem:
    motivo = "span mancante"
    span = event.span or ""
    return QuarantenaItem(
        id=quarantena_id(chunk.doc_id, chunk.testo, span, motivo),
        frammento=event.lemma or span or chunk.testo,
        motivo=motivo,
        ancora_doc=chunk.doc_id,
        ancora_chunk=chunk.id,
        ancora_span=span or None,
        versione_regole=RULESET_VERSION,
    )


def _skip_mint_mention(forma: str, tipo: str) -> bool:
    """Do not coin a :Menzione for clause-like or predicate-like fillers."""
    text = (forma or "").strip()
    if not text:
        return False
    if any(ch in text for ch in ".!?«»\"“”"):
        return True
    words = text.split()
    if len(words) > 12:
        return True
    if tipo == "pronome" and len(words) > 2:
        return True
    if words[0].casefold() in {"di", "da", "per", "a", "ad", "con", "su", "in", "tra"} and len(words) > 3:
        return True
    folded = " ".join(w.casefold() for w in words)
    if " e " in f" {folded} " or " and " in f" {folded} ":
        return True
    if tipo in {"nome_proprio", "sn_comune"} and len(words) == 1:
        token = words[0]
        if token[:1].islower():
            return True
    first = words[0].casefold()
    if first.endswith(("are", "ere", "ire", "rre", "arsi", "ersi", "irsi")) and len(words) > 1:
        return True
    return False


def _resolve_event(
    event: EventoGrezzo,
    chunk: PeriodChunk,
    posizione_chunk: int,
    posizione_doc: int,
    mention_start: int,
) -> tuple[EventoRisolto, list[MenzioneRisolta]]:
    menzioni: list[MenzioneRisolta] = []
    argomenti: list[ArgomentoRisolto] = []
    minted_count = 0
    for arg in event.argomenti:
        forma_canonica = (arg.forma or "").strip()
        if _skip_mint_mention(forma_canonica, arg.tipo_superficiale):
            argomenti.append(
                ArgomentoRisolto(
                    ruolo=arg.ruolo,
                    menzione_id="",
                    preposizione=arg.preposizione,
                )
            )
            continue
        indice_menzione = mention_start + minted_count
        minted = menzione_id(
            forma_canonica,
            arg.tipo_superficiale,
            chunk.doc_id,
            chunk.id,
            indice_menzione,
        )
        menzioni.append(
            MenzioneRisolta(
                id=minted.id,
                forma=arg.forma,
                forma_canonica=forma_canonica,
                numero=arg.numero,
                genere=arg.genere,
                tipo_superficiale=arg.tipo_superficiale,
                non_risolto=minted.non_risolto,
                documento=chunk.doc_id,
                chunk_id=chunk.id,
                regola=_REGOLA,
                versione_regole=RULESET_VERSION,
            )
        )
        minted_count += 1
        argomenti.append(
            ArgomentoRisolto(
                ruolo=arg.ruolo,
                menzione_id=minted.id,
                preposizione=arg.preposizione,
            )
        )

    resolved = EventoRisolto(
        id=evento_id(chunk.doc_id, chunk.testo, posizione_chunk),
        lemma=event.lemma,
        tempo=event.tempo,
        polarita="negata" if event.polarita_negata else "affermata",
        polarita_negata=event.polarita_negata,
        modalizzato=event.modalizzato,
        modalizzato_forma=event.modalizzato_forma,
        modalita=getattr(event, "modalita", "fattuale") or "fattuale",
        e_testa=bool(getattr(event, "e_testa", False)),
        offset_inizio=getattr(event, "offset_inizio", None),
        offset_fine=getattr(event, "offset_fine", None),
        iterativita=event.iterativo,
        iterativo=event.iterativo,
        fattualita=None,
        piano=None,
        documento=chunk.doc_id,
        chunk_id=chunk.id,
        indice_chunk=posizione_chunk,
        posizione_doc=posizione_doc,
        posizione_chunk=posizione_chunk,
        ancora=event.span,
        segmentazione=event.segmentazione,
        sogg_speciale=event.sogg_speciale,
        tempo_assoluto_grezzo=event.tempo_assoluto_grezzo,
        regola=_REGOLA,
        versione_regole=RULESET_VERSION,
        argomenti=argomenti,
        ruolo_se=event.ruolo_se,
        completiva_di=event.completiva_di,
        indice_grezzo=event.indice,
        classe_verbo_reggente=event.classe_verbo_reggente,
        finale=event.finale,
        frase_tipo=event.frase_tipo,
        marca_dialogo=event.marca_dialogo,
        frase_indice=event.frase_indice,
        avverbio_temporale_esplicito=event.avverbio_temporale_esplicito,
        connettivo_sequenziale_esplicito=event.connettivo_sequenziale_esplicito,
        span=event.span,
    )
    return resolved, menzioni


def _remap_predicati(
    predicati: list[PredicatoNonFinito],
    eventi: list[EventoRisolto],
) -> list[PredicatoNonFinito]:
    """Copy free non-finites, remapping governo_indice via indice_grezzo."""
    by_grezzo = {
        event.indice_grezzo: event
        for event in eventi
        if event.indice_grezzo is not None
    }
    testa = next((event for event in eventi if event.e_testa), None)
    remapped: list[PredicatoNonFinito] = []
    for pred in predicati:
        gov = pred.governo_indice
        if gov is not None and gov not in by_grezzo:
            gov = testa.indice_grezzo if testa is not None else None
        remapped.append(pred.model_copy(update={"governo_indice": gov}))
    return remapped


def risolvi(
    factsheet: ChunkFactsheet,
    chunk: PeriodChunk,
    posizione_doc: int | None = None,
) -> SegmentationResult:
    """Resolve a factsheet into events, minted mentions, and quarantena items.

    ``posizione_doc`` defaults to ``chunk.ordinale``. Empty factsheets (failed
    extraction) yield no events; factsheet quarantena is still returned so M8
    can merge it later.
    """
    doc_pos = chunk.ordinale if posizione_doc is None else posizione_doc
    quarantena = [frammento_to_item(item, chunk) for item in factsheet.quarantena]
    quarantined_spans = {
        item.span for item in factsheet.quarantena if item.span
    }

    if not factsheet.eventi:
        return SegmentationResult(
            eventi=[],
            menzioni=[],
            quarantena=quarantena,
            predicati_non_finiti=_remap_predicati(
                list(getattr(factsheet, "predicati_non_finiti", []) or []),
                [],
            ),
        )

    usable: list[EventoGrezzo] = []
    for event in factsheet.eventi:
        if event.span in quarantined_spans:
            continue
        if not _has_span(event):
            quarantena.append(_span_quarantena(event, chunk))
            continue
        usable.append(event)

    ordered = _order_events(usable, chunk.testo)
    eventi: list[EventoRisolto] = []
    menzioni: list[MenzioneRisolta] = []
    mention_index = 0
    for posizione_chunk, event in enumerate(ordered):
        resolved, minted = _resolve_event(
            event, chunk, posizione_chunk, doc_pos, mention_index
        )
        eventi.append(resolved)
        menzioni.extend(minted)
        mention_index += len(minted)
    return SegmentationResult(
        eventi=eventi,
        menzioni=menzioni,
        quarantena=quarantena,
        predicati_non_finiti=_remap_predicati(
            list(getattr(factsheet, "predicati_non_finiti", []) or []),
            eventi,
        ),
    )


def _as_chunk_factsheet(factsheet: ChunkFactsheet | FraseFactsheet) -> ChunkFactsheet:
    if isinstance(factsheet, ChunkFactsheet):
        return factsheet
    return ChunkFactsheet(
        eventi=list(factsheet.eventi),
        archi=list(factsheet.archi),
        quarantena=list(factsheet.quarantena),
        predicati_non_finiti=list(factsheet.predicati_non_finiti),
    )


def _zona_attr(zona: object | None, *names: str, default: str = "") -> str:
    if zona is None:
        return default
    for name in names:
        value = getattr(zona, name, None)
        if value is not None and str(value) != "":
            return str(value)
    return default


def risolvi_unita(
    factsheet: ChunkFactsheet | FraseFactsheet,
    unita: UnitaTesto,
    zona: object | None = None,
    posizione_doc: int | None = None,
) -> SegmentationResult:
    """Same span/id logic as ``risolvi``, with document offsets and unit text.

    ``unita.testo`` is the span-search / id-minting "chunk" text. Events in the
    same zone share ``zona.id`` as ``chunk_id`` so intra-zone Fusione can apply.
    Mention minting uses a unit-scoped chunk id so non-proper-name ids stay unique.
    """
    zona_id = _zona_attr(zona, "id") or (unita.zona_id or "")
    doc_id = _zona_attr(zona, "documento", "doc_id")
    mint_id = f"{zona_id}:{unita.indice}" if zona_id else f"u:{unita.indice}"
    chunk = PeriodChunk(
        id=mint_id,
        doc_id=doc_id,
        ordinale=unita.indice,
        testo=unita.testo,
    )
    doc_pos = unita.offset_inizio if posizione_doc is None else posizione_doc
    result = risolvi(_as_chunk_factsheet(factsheet), chunk, posizione_doc=doc_pos)
    for event in result.eventi:
        if zona_id:
            event.chunk_id = zona_id
        # Document offset keeps ids unique when two units share the same text.
        event.id = evento_id(
            doc_id,
            f"{unita.offset_inizio}|{unita.testo}",
            event.posizione_chunk if event.posizione_chunk is not None else 0,
        )
    if zona_id:
        for mention in result.menzioni:
            mention.chunk_id = zona_id
    return result


__all__ = ["SegmentationResult", "risolvi", "risolvi_unita"]
