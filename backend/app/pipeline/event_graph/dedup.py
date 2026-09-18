"""MICRO stages 0–2 — per-zone event extraction, then dedup (Addendum 4).

Extraction now runs ``extraction_simple.extract_event_entities`` **once per
zone** ("chunk") instead of the retired per-sentence grammatical extraction:
one independent-clause event + participants (SOGG/OGG) + temporal locators
(TEMPO). List numbers and date prefixes stay inside the event span. Dedup
(mention/event coref) is unchanged and always runs before inter-sentence
linking (stages 3/4), which this module does not call.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

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
from app.pipeline.event_graph.tempo_entita import classifica_entita_temporale
from app.pipeline.event_graph.tempo_iso import collocazione_da_espressione
from app.pipeline.event_graph.entita_forma import (
    contesto_entita,
    e_entita_ammessa,
    nome_grezzo,
    pulisci_forma,
    summary_grezzo,
)
from app.pipeline.event_graph.ids import evento_id, menzione_id
from app.pipeline.event_graph.segmentation import SegmentationResult

STAGE_ORDER = ("preprocess", "extract", "dedup", "pair", "nonadj", "allen")
STAGES_FINO_DEDUP = STAGE_ORDER[: STAGE_ORDER.index("dedup") + 1]

REGOLA = "extraction_simple.extract_event_entities"

EstraiEventi = Callable[..., Awaitable[EventEntityExtractionResult]]

_PROPER_START = re.compile(r"^[A-ZÀ-Þ]")

_MESI = (
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_MESI_RE = "|".join(_MESI)
_DATA_RE = re.compile(rf"\b\d{{1,2}}\s+(?:{_MESI_RE})\s+\d{{4}}\b", re.IGNORECASE)
_ISO_DATA_RE = re.compile(
    r"\b\d{4}-\d{2}(?:-\d{2}(?:[Tt ]\d{2}(?::\d{2}(?::\d{2})?)?)?)?\b"
)
_DATA_NUMERICA_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b")
_ORA_RE = re.compile(r"\b(?:ore\s+)?\d{1,2}[:.]\d{2}\b", re.IGNORECASE)
_DECENNIO_RE = re.compile(
    r"\b(?:(?:negli|nei|in|the)\s+)?anni\s+['’]?\d0(?:s)?"
    r"|(?:the\s+)?['’]\d0s"
    r"|anni\s+(?:venti|trenta|quaranta|cinquanta|sessanta|"
    r"settanta|ottanta|novanta)\b",
    re.IGNORECASE,
)
_VAGA_POCO_RE = re.compile(
    r"\b(?:dopo\s+un\s+po['’]?|dopo\s+poco|after\s+a\s+while|"
    r"after\s+a\s+bit|un\s+po['’]?\s+dopo)\b",
    re.IGNORECASE,
)
_SCADENZA_RE = re.compile(
    r"\b(?:entro\s+(?:il|la|lo|l['’]|the)\s+\S+|scadenza\s+\S+|"
    r"deadline\s+\S+|due\s+by\s+\S+)\b",
    re.IGNORECASE,
)
_INTERVALLO_DAL_AL_RE = re.compile(
    rf"\b(?:dal|da)\s+\d{{1,2}}\s+(?:{_MESI_RE})\s+\d{{4}}\s+al\s+"
    rf"\d{{1,2}}\s+(?:{_MESI_RE})(?:\s+\d{{4}})?\b",
    re.IGNORECASE,
)
_INTERVALLO_TRA_RE = re.compile(r"\btra\s+\d{4}\s+e\s+\d{4}\b", re.IGNORECASE)
_INTERVALLO_ANNI_RE = re.compile(r"\b(?:1[0-9]{3}|20[0-9]{2})\s*[-–]\s*(?:1[0-9]{3}|20[0-9]{2})\b")
_RELATIVA_PAROLA_RE = re.compile(
    r"\b(?:ieri|oggi|domani|yesterday|today|tomorrow|"
    r"questa\s+sera|quel(?:la)?\s+sera|vigilia(?:\s+di\s+\w+)?)\b",
    re.IGNORECASE,
)
_TEMPO_PATTERN = (
    _INTERVALLO_DAL_AL_RE,
    _INTERVALLO_TRA_RE,
    _INTERVALLO_ANNI_RE,
    _DATA_RE,
    _ISO_DATA_RE,
    _DATA_NUMERICA_RE,
    _ORA_RE,
    _SCADENZA_RE,
    _DECENNIO_RE,
    _VAGA_POCO_RE,
    _RELATIVA_PAROLA_RE,
)
_TEMPO_PREFIX_TOKENS = frozenset(
    {
        *_MESI,
        "dopo",
        "prima",
        "circa",
        "ore",
        "ora",
        "minuti",
        "minuto",
        "secondi",
        "giorni",
        "giorno",
        "settimane",
        "settimana",
        "mesi",
        "mese",
        "anni",
        "anno",
        "e",
        "mezzo",
        "il",
        "la",
        "lo",
        "alle",
        "al",
        "del",
        "della",
        "di",
        "dal",
        "al",
        "tra",
        "ieri",
        "oggi",
        "domani",
        "yesterday",
        "today",
        "tomorrow",
        "questa",
        "quel",
        "quella",
        "sera",
        "vigilia",
        "natale",
        "nel",
        "nella",
        "un",
        "una",
        "after",
        "before",
        "about",
        "hours",
        "hour",
        "minutes",
        "minute",
        "seconds",
        "days",
        "day",
        "weeks",
        "week",
        "months",
        "month",
        "years",
        "year",
        "and",
        "the",
        "at",
        "on",
        "in",
    }
)


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


def _is_prefisso_lista_o_tempo(prefix: str) -> bool:
    """True when the text before a match is only a list marker and/or a date."""
    if not prefix or not prefix.strip():
        return False
    if re.fullmatch(r"[\s\d.,;:/'’\-—–]+", prefix):
        return True
    for token in re.findall(r"[A-Za-zÀ-ÿ']+|\d+", prefix):
        if token.isdigit():
            continue
        if token.casefold() in _TEMPO_PREFIX_TOKENS:
            continue
        return False
    return True


def _estendi_span_evento(haystack: str, start: int, end: int) -> tuple[int, int]:
    """Pull line-initial list numbers and date prefixes into the event span."""
    if start < 0 or end < start or start > len(haystack):
        return start, end
    line_start = haystack.rfind("\n", 0, start) + 1
    while line_start < start and haystack[line_start] in " \t":
        line_start += 1
    prefix = haystack[line_start:start]
    if _is_prefisso_lista_o_tempo(prefix):
        return line_start, end
    return start, end


def _looks_like_tempo(item: object) -> bool:
    text = nome_grezzo(item)
    if not text:
        return False
    return any(pattern.search(text) for pattern in _TEMPO_PATTERN)


def _raccogli_espressioni_tempo(testo: str) -> list[str]:
    spans: list[tuple[int, int, str]] = []
    for pattern in _TEMPO_PATTERN:
        for match in pattern.finditer(testo or ""):
            piece = match.group(0).strip()
            if piece:
                spans.append((match.start(), match.end(), piece))
    spans.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    tenuti: list[tuple[int, int, str]] = []
    for start, end, piece in spans:
        contenuto = any(
            gia_s <= start and end <= gia_e and (gia_s, gia_e) != (start, end)
            for gia_s, gia_e, _ in tenuti
        )
        if contenuto:
            continue
        tenuti.append((start, end, piece))
    found: list[str] = []
    seen: set[str] = set()
    for _start, _end, piece in tenuti:
        key = piece.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(piece)
    return found


def _nomi_tempo(part: EventEntityParticipation, evento_testo: str) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in (
        *(part.tempo or []),
        *(item for item in (part.entities or []) if _looks_like_tempo(item)),
        *_raccogli_espressioni_tempo(evento_testo),
    ):
        grezzo = nome_grezzo(raw) if not isinstance(raw, str) else raw.strip()
        classificato = classifica_entita_temporale(grezzo)
        if classificato.tipo is None:
            continue
        grezzo = classificato.forma or grezzo
        if not e_entita_ammessa(grezzo, temporale=True):
            continue
        nome = pulisci_forma(grezzo, temporale=True) or grezzo
        key = nome.casefold()
        if not nome or key in seen:
            continue
        seen.add(key)
        summary = contesto_entita(
            evento_testo,
            grezzo,
            nome,
            summary_grezzo(raw) if not isinstance(raw, str) else "",
        )
        ordered.append((nome, summary))
    return ordered


def _iso_da_tempi(tempi: Sequence[tuple[str, str] | str]) -> str | None:
    """Best calendar ISO already present in the TEMPO mentions, or None."""
    migliore = None
    for item in tempi:
        nome = item[0] if isinstance(item, tuple) else item
        parsed = collocazione_da_espressione(nome)
        if parsed is None:
            continue
        if migliore is None:
            migliore = parsed
            continue
        if parsed.precisione in {"giorno", "ora", "minuto", "secondo"} and migliore.precisione in {
            "anno",
            "mese",
        }:
            migliore = parsed
        elif parsed.precisione in {"ora", "minuto", "secondo"} and migliore.precisione == "giorno":
            migliore = parsed
    return None if migliore is None else migliore.canonico


def _tipo_superficiale(nome: str) -> str:
    """Cheap heuristic: capitalized → nome_proprio, else sn_comune.

    Does not change identity (``ids.menzione_id`` hashes both the same way
    on the normalised form) — only the persisted surface-type label.
    """
    return "nome_proprio" if _PROPER_START.match(nome or "") else "sn_comune"


def _dedup_entities(
    entities: Sequence[object] | None, evento_testo: str
) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for raw in entities or []:
        grezzo = nome_grezzo(raw)
        if not grezzo:
            continue
        if not e_entita_ammessa(grezzo, temporale=False):
            continue
        nome = pulisci_forma(grezzo, temporale=False)
        if not nome:
            continue
        key = nome.casefold()
        if key in seen:
            continue
        seen.add(key)
        summary = contesto_entita(
            evento_testo, grezzo, nome, summary_grezzo(raw)
        )
        out.append((nome, summary if summary else grezzo))
    return out


def _crea_menzione(
    nome: str,
    summary: str,
    *,
    tipo: str,
    doc_id: str,
    zona_id: str,
    pos: int,
    evento_id_val: str,
) -> MenzioneRisolta:
    minted = menzione_id(nome, tipo, doc_id, zona_id, pos)
    menzione = MenzioneRisolta(
        id=minted.id,
        forma=nome,
        forma_canonica=nome,
        tipo_superficiale=tipo,  # type: ignore[arg-type]
        non_risolto=minted.non_risolto,
        documento=doc_id,
        chunk_id=zona_id,
        regola=REGOLA,
        versione_regole=RULESET_VERSION,
        summary=summary,
    )
    menzione.registra(evento_id_val, summary)
    return menzione


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
    eid = evento_id(doc_id, zona_testo, indice)
    tempi = _nomi_tempo(part, evento_testo)
    tempo_keys = {nome.casefold() for nome, _summary in tempi}
    entita = [
        (nome, summary)
        for nome, summary in _dedup_entities(part.entities, evento_testo)
        if nome.casefold() not in tempo_keys
    ]

    menzioni: list[MenzioneRisolta] = []
    argomenti: list[ArgomentoRisolto] = []
    for pos, (nome, summary) in enumerate(entita):
        tipo = _tipo_superficiale(nome)
        menzione = _crea_menzione(
            nome,
            summary,
            tipo=tipo,
            doc_id=doc_id,
            zona_id=unita.zona_id or "",
            pos=pos,
            evento_id_val=eid,
        )
        menzioni.append(menzione)
        argomenti.append(
            ArgomentoRisolto(
                ruolo="SOGG" if pos == 0 else "OGG",
                menzione_id=menzione.id,
            )
        )
    for pos, (nome, summary) in enumerate(tempi):
        tipo = _tipo_superficiale(nome)
        menzione = _crea_menzione(
            nome,
            summary,
            tipo=tipo,
            doc_id=doc_id,
            zona_id=unita.zona_id or "",
            pos=len(entita) + pos,
            evento_id_val=eid,
        )
        menzioni.append(menzione)
        argomenti.append(
            ArgomentoRisolto(
                ruolo="TEMPO",
                menzione_id=menzione.id,
            )
        )

    evento = EventoRisolto(
        id=eid,
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
        tempo_assoluto=_iso_da_tempi(tempi),
        tempo_assoluto_grezzo=tempi[0][0] if tempi else None,
        avverbio_temporale_esplicito=bool(tempi),
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
    2. one ``EventoRisolto`` per participation (SOGG/OGG from entities, TEMPO
       from ``tempo`` plus date/hour harvest; list-number and date prefixes
       glued back onto the span) + one ``UnitaTesto`` per event, so
       ``sentence_pair_linking``'s head/adjacency lookup keeps working.
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
        logger.exception(
            "extract_event_entities failed zona=%s doc=%s", zona_id, doc_id
        )
        return DedupResult(sotto=sotto, esiti=esiti, unita=[], predicati_non_finiti=[])

    units: list[UnitaTesto] = []
    cursor = 0
    previous_testo: str | None = None

    for indice, part in enumerate(result.participations or []):
        evento_testo = (part.event or "").strip()
        if not evento_testo:
            continue
        start, end = _find_offset(zona_testo, evento_testo, cursor)
        start, end = _estendi_span_evento(zona_testo, start, end)
        evento_testo = zona_testo[start:end]
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
