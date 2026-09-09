"""MICRO stage 1 — one structured LLM call per sentence (Addendum 2 M-micro1).

1a–1d in a single call. Dialogo units skip the LLM. Self-check §12 is a
deterministic Python checklist; at most one correction call (cap 2).
The 3-phase-per-chunk agent is no longer the main path. ``estrai()`` remains
as a compatibility wrapper that still returns ``ExtractionOutcome`` /
``ChunkFactsheet`` for the existing pipeline until M-flash.
"""

from __future__ import annotations

import inspect
import json
import re
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.models.event_graph import (
    ArcoEventoGrezzo,
    ArgomentoGrezzo,
    ChunkFactsheet,
    ClasseVerboReggente,
    EventoGrezzo,
    FrammentoQuarantena,
    FraseFactsheet,
    FraseTipo,
    PredicatoNonFinito,
    QuarantenaItem,
    RelazioneSegnale,
    RuoloSe,
    Segmentazione,
    SoggSpeciale,
    TempoVerbale,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.chunking_periods import (
    PeriodChunk,
    UnitaTesto,
    preprocess_zona,
)
from app.pipeline.event_graph.config import settings
from app.pipeline.event_graph.extraction_prompts import (
    SYSTEM_FRASE,
    SYSTEM_FRASE_CORREZIONE,
    user_frase,
    user_frase_correzione,
)
from app.pipeline.event_graph.ids import quarantena_id
from app.pipeline.event_graph.infra.driver import get_session
from app.pipeline.event_graph.infra.llm import call_structured
from app.pipeline.event_graph.text_norm import fold_text

_MAX_CALLS_PER_SENTENCE = 2

_MATCH_FACTSHEET = (
    "MATCH (c:EgChunk {id: $id}) RETURN c.factsheet_json, c.factsheet_versione"
)
_MERGE_CHUNK = (
    "MERGE (c:EgChunk {id: $id}) "
    "SET c.doc_id=$doc_id, c.ordinale=$ordinale, c.testo=$testo, "
    "c.factsheet_json=$json, c.factsheet_versione=$ver, c.factsheet_modello=$model"
)
_MATCH_FRASE = (
    "MATCH (u:EgUnita {zona_id: $zona_id, indice: $indice}) "
    "RETURN u.factsheet_json, u.factsheet_versione"
)
_MERGE_FRASE = (
    "MERGE (u:EgUnita {zona_id: $zona_id, indice: $indice}) "
    "SET u.factsheet_json=$json, u.factsheet_versione=$ver, "
    "u.factsheet_modello=$model, u.testo=$testo, u.tipo=$tipo"
)


# Kept for import compatibility with older tests; not used on the main path.
class Phase1Event(BaseModel):
    indice: int
    lemma: str
    span: str
    tempo: TempoVerbale
    segmentazione: Segmentazione
    polarita_negata: bool
    modalizzato: bool
    modalizzato_forma: str | None = None
    iterativo: bool
    tempo_assoluto_grezzo: str | None = None
    sogg_speciale: SoggSpeciale = "nessuno"
    ruolo_se: RuoloSe = "nessuno"
    finale: bool = False
    frase_tipo: FraseTipo = "dichiarativa"


class Phase1Result(BaseModel):
    eventi: list[Phase1Event] = Field(default_factory=list)


class Phase2Event(BaseModel):
    indice: int
    argomenti: list[ArgomentoGrezzo] = Field(default_factory=list)
    completiva_di: int | None = None
    classe_verbo_reggente: ClasseVerboReggente = "nessuna"
    marca_dialogo: bool = False
    frase_indice: int = 0
    avverbio_temporale_esplicito: bool = False
    connettivo_sequenziale_esplicito: bool = False
    sogg_speciale: SoggSpeciale | None = None
    ruolo_se: RuoloSe | None = None
    finale: bool | None = None


class Phase2Result(BaseModel):
    eventi: list[Phase2Event] = Field(default_factory=list)
    archi: list[ArcoEventoGrezzo] = Field(default_factory=list)
    quarantena: list[FrammentoQuarantena] = Field(default_factory=list)


@dataclass
class ChecklistViolation:
    kind: str
    motivo: str
    indice: int | None = None
    da_indice: int | None = None
    a_indice: int | None = None


@dataclass
class ExtractionOutcome:
    factsheet: ChunkFactsheet
    quarantena: list[QuarantenaItem] = field(default_factory=list)
    llm_calls: int = 0
    reused: bool = False


@dataclass
class FraseExtractionOutcome:
    factsheet: FraseFactsheet
    llm_calls: int = 0
    reused: bool = False


@dataclass
class _ZonaShim:
    id: str
    testo: str
    offset_inizio: int = 0


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def session_run(session: Any, query: str, parameters: dict | None = None, **kwargs: Any) -> Any:
    """Run a Cypher query on a real AsyncSession or a test FakeSession.

    Accepts ``run(query, **params)`` and ``run(query, params)``.
    """
    params = {**(parameters or {}), **kwargs}
    try:
        result = session.run(query, **params)
    except TypeError:
        result = session.run(query, params)
    return await _maybe_await(result)


async def _consume_write(result: Any) -> None:
    """Finish an auto-commit write; otherwise MERGE never lands on the server."""
    consume = getattr(result, "consume", None)
    if callable(consume):
        await _maybe_await(consume())


@asynccontextmanager
async def _cache_session(session: Any) -> AsyncIterator[Any]:
    """Short-lived Neo4j session for factsheet cache. Never held across LLM calls."""
    if session is not None:
        yield session
        return
    try:
        async with get_session() as owned:
            yield owned
    except RuntimeError:
        yield None


def _record_mapping(record: Any) -> Mapping[str, Any]:
    if record is None:
        return {}
    keys_fn = getattr(record, "keys", None)
    if callable(keys_fn):
        try:
            return {key: record[key] for key in keys_fn()}
        except Exception:
            pass
    if isinstance(record, dict):
        return record
    data_fn = getattr(record, "data", None)
    if callable(data_fn):
        try:
            data = data_fn()
        except TypeError:
            data = None
        if isinstance(data, dict):
            return data
    if isinstance(record, Mapping):
        try:
            return dict(record)
        except Exception:
            return {}
    return {}


def _mapping_get(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        try:
            value = data[key]
        except Exception:
            getter = getattr(data, "get", None)
            if not callable(getter):
                continue
            try:
                value = getter(key)
            except Exception:
                continue
        if value is not None:
            return value
    return None


def _parse_chunk_factsheet(raw: Any) -> ChunkFactsheet | None:
    if raw is None or raw == "":
        return None
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        return ChunkFactsheet.model_validate(payload)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _parse_frase_factsheet(raw: Any) -> FraseFactsheet | None:
    if raw is None or raw == "":
        return None
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        return FraseFactsheet.model_validate(payload)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


async def load_cached_factsheet(session: Any, chunk_id: str) -> ChunkFactsheet | None:
    result = await session_run(session, _MATCH_FACTSHEET, id=chunk_id)
    single_fn = getattr(result, "single", None)
    if single_fn is None:
        return None
    record = await _maybe_await(single_fn())
    data = _record_mapping(record)
    raw = _mapping_get(data, "c.factsheet_json", "factsheet_json")
    versione = _mapping_get(data, "c.factsheet_versione", "factsheet_versione")
    if raw is None or versione != RULESET_VERSION:
        return None
    return _parse_chunk_factsheet(raw)


async def load_cached_frase(
    session: Any, zona_id: str, indice: int
) -> FraseFactsheet | None:
    result = await session_run(
        session, _MATCH_FRASE, zona_id=zona_id, indice=indice
    )
    single_fn = getattr(result, "single", None)
    if single_fn is None:
        return None
    record = await _maybe_await(single_fn())
    data = _record_mapping(record)
    raw = _mapping_get(data, "u.factsheet_json", "factsheet_json")
    versione = _mapping_get(data, "u.factsheet_versione", "factsheet_versione")
    if raw is None or versione != RULESET_VERSION:
        return None
    return _parse_frase_factsheet(raw)


def _dump_factsheet(factsheet: ChunkFactsheet | FraseFactsheet) -> str:
    return factsheet.model_dump_json()


async def persist_chunk(session: Any, chunk: PeriodChunk, factsheet: ChunkFactsheet) -> None:
    result = await session_run(
        session,
        _MERGE_CHUNK,
        id=chunk.id,
        doc_id=chunk.doc_id,
        ordinale=chunk.ordinale,
        testo=chunk.testo,
        json=_dump_factsheet(factsheet),
        ver=RULESET_VERSION,
        model=settings.OPENAI_MODEL,
    )
    await _consume_write(result)


async def persist_frase(session: Any, unita: UnitaTesto, factsheet: FraseFactsheet) -> None:
    if not unita.zona_id:
        return
    result = await session_run(
        session,
        _MERGE_FRASE,
        zona_id=unita.zona_id,
        indice=unita.indice,
        json=_dump_factsheet(factsheet),
        ver=RULESET_VERSION,
        model=settings.OPENAI_MODEL,
        testo=unita.testo,
        tipo=unita.tipo,
    )
    await _consume_write(result)


def _nesting_ok(left: EventoGrezzo | None, right: EventoGrezzo | None) -> bool:
    if left is None or right is None:
        return True
    if left.completiva_di is None and right.completiva_di is None:
        return True
    if (
        left.completiva_di is not None
        and right.completiva_di is not None
        and left.completiva_di == right.completiva_di
    ):
        return True
    if left.completiva_di == right.indice or right.completiva_di == left.indice:
        return True
    return False


_SENTENCE_MARKS = frozenset(".!?;")
_QUOTE_OPENERS = ("«", "“", '"', "»", "”")
_ASPECTUAL_LEMMAS = frozenset(
    {
        "begin",
        "cominciare",
        "continue",
        "continuare",
        "finire",
        "finish",
        "iniziare",
        "smettere",
        "start",
        "stop",
    }
)
_CAUSATIVE_LEMMAS = frozenset(
    {
        "cause",
        "causare",
        "fare",
        "lasciare",
        "let",
        "make",
        "provocare",
    }
)
_VOLITIVE_LEMMAS = frozenset({"volere", "want", "wish", "intend"})
_DEONTIC_LEMMAS = frozenset(
    {"dovere", "potere", "must", "can", "could", "should", "may", "might"}
)
_IRREGULAR_IT_LEMMAS = {
    "arrendersi": "arrendere",
    "dirsi": "dire",
    "farsi": "fare",
    "porsi": "porre",
    "protendersi": "protendere",
    "torsi": "torcere",
    "tolgere": "togliere",
    "vedersi": "vedere",
}
_ESSERE_FORMS = frozenset(
    {
        "be",
        "is",
        "are",
        "was",
        "were",
        "essere",
        "è",
        "e'",
        "era",
        "ero",
        "eri",
        "erano",
        "sono",
        "sei",
        "siamo",
        "siete",
        "fu",
        "fui",
        "furono",
        "sia",
        "siano",
        "fosse",
        "fossero",
        "sarà",
        "saranno",
    }
)
_ADVERB_LEMMAS = frozenset(
    {
        "easily",
        "facilmente",
        "gently",
        "immediately",
        "lentamente",
        "slowly",
    }
)
_FINITE_SEGS = frozenset(
    {"principale_finita", "subordinata_finita", "coordinata_finita"}
)
_MAIN_FINITE_SEGS = frozenset({"principale_finita", "coordinata_finita"})
_IT_INFINITIVE_RE = re.compile(r"(are|ere|ire|rre|arsi|ersi|irsi)$", re.IGNORECASE)
_INTERPOSED_RE = re.compile(r"^\s*(a|ad|di|per|to)?\s*$", re.IGNORECASE)
_IT_MARKERS = frozenset(
    {
        "il",
        "lo",
        "la",
        "gli",
        "le",
        "un",
        "uno",
        "una",
        "del",
        "della",
        "dei",
        "delle",
        "che",
        "non",
        "per",
        "con",
        "è",
        "sono",
        "nel",
        "nella",
        "questo",
        "questa",
        "quello",
        "quella",
        "di",
        "da",
        "si",
    }
)
_EN_MARKERS = frozenset(
    {"the", "is", "was", "were", "are", "this", "that", "with", "from"}
)
_PAST_TEMPI = frozenset({"passato", "trapassato", "imperfetto"})
_CONDIZIONALE_MARKERS = ("rebbe", "rebbero", "would", "could", "should")


def _is_adverb_lemma(lemma: str) -> bool:
    text = (lemma or "").strip().casefold()
    if not text:
        return False
    if text in _ADVERB_LEMMAS:
        return True
    if text.endswith("mente") and len(text) > 6:
        return True
    return text.endswith("ly") and len(text) > 4


def _span_inside_quotes(sentence: str, span: str) -> bool:
    """True when the event span sits inside «…» / “…” of this sentence."""
    if not sentence or not span:
        return False
    idx = sentence.find(span)
    if idx < 0:
        return False
    before = sentence[:idx]
    pairs = (("«", "»"), ("“", "”"), ('"', '"'))
    for opener, closer in pairs:
        if opener == closer:
            if before.count(opener) % 2 == 1:
                return True
            continue
        if before.count(opener) > before.count(closer):
            return True
    return False


def _lemma_norm(lemma: str) -> str:
    return (lemma or "").strip().casefold()


def _normalize_it_lemma(lemma: str) -> str:
    folded = _lemma_norm(lemma)
    return _IRREGULAR_IT_LEMMAS.get(folded, lemma)


def _is_italian_infinitive(lemma: str) -> bool:
    return bool(_IT_INFINITIVE_RE.search((lemma or "").strip()))


def _is_italian_text(testo: str) -> bool:
    if not testo or not testo.strip():
        return False
    tokens = re.findall(r"[A-Za-zÀ-ÿ']+", testo.casefold())
    if any(ch in testo for ch in "àèéìòùÀÈÉÌÒÙ"):
        return True
    it_hits = sum(1 for token in tokens if token in _IT_MARKERS)
    en_hits = sum(1 for token in tokens if token in _EN_MARKERS)
    if it_hits and it_hits >= en_hits:
        return True
    return False


def _looks_inflected_italian(lemma: str) -> bool:
    text = (lemma or "").strip().casefold()
    if not text or _is_italian_infinitive(text):
        return False
    if any(ch in text for ch in "àèéìòù"):
        return True
    return bool(re.search(r"(ò|à|ì|arono|irono|avano|ivano|eva|ava|iva|ette|etti|sse)$", text))


def _needs_italian_infinitive(lemma: str, testo: str = "") -> bool:
    if _is_italian_text(testo):
        return True
    return _looks_inflected_italian(lemma)


def _tempo_finite(event: EventoGrezzo) -> bool:
    return event.frase_tipo == "imperativa" or event.tempo != "non_finito"


def _seg_finite(event: EventoGrezzo) -> bool:
    return event.segmentazione in _FINITE_SEGS


def _signals_conflict(event: EventoGrezzo) -> bool:
    return _tempo_finite(event) != _seg_finite(event)


def _is_finite_candidate(event: EventoGrezzo) -> bool:
    """A1a: finite iff (imperativa or tempo != non_finito) AND finite segmentation."""
    return _tempo_finite(event) and _seg_finite(event)


def _span_first_token(span: str) -> str:
    return (re.findall(r"[A-Za-zÀ-ÿ']+", span or "") or [""])[0]


def _is_non_finite_event(event: EventoGrezzo) -> bool:
    """A1c: signal conflict is treated as non-finite."""
    if _signals_conflict(event):
        return True
    first = _span_first_token(event.span or "")
    if (
        first
        and _is_italian_infinitive(first)
        and _lemma_norm(first) == _lemma_norm(event.lemma)
        and event.tempo in {"presente", "non_finito", "futuro"}
    ):
        return True
    return not _is_finite_candidate(event)


def _is_condizionale(event: EventoGrezzo) -> bool:
    forma = (event.modalizzato_forma or "").casefold()
    if "condizional" in forma:
        return True
    return any(marker in forma for marker in _CONDIZIONALE_MARKERS)


def _governor_kind(event: EventoGrezzo) -> str | None:
    lemma = _lemma_norm(event.lemma)
    if lemma in _ASPECTUAL_LEMMAS:
        return "aspectual"
    if lemma in _CAUSATIVE_LEMMAS:
        return "causative"
    if event.modalita == "volitivo" or lemma in _VOLITIVE_LEMMAS:
        return "volitivo"
    if event.modalita == "deontico" or lemma in _DEONTIC_LEMMAS:
        return "deontico"
    if event.modalita and event.modalita != "fattuale":
        return "modal"
    return None


def _should_collapse_governor(event: EventoGrezzo) -> bool:
    return _governor_kind(event) is not None


def _is_viable_governor(event: EventoGrezzo) -> bool:
    return not _is_non_finite_event(event)


def _span_pos(testo: str, span: str) -> tuple[int, int] | None:
    if not testo or not span:
        return None
    start = testo.find(span)
    if start < 0:
        return None
    return start, start + len(span)


def _interposed_ok(testo: str, governor: EventoGrezzo, child: EventoGrezzo) -> bool:
    gov_pos = _span_pos(testo, governor.span or "")
    child_pos = _span_pos(testo, child.span or "")
    if gov_pos is None or child_pos is None:
        return False
    _, gov_end = gov_pos
    child_start, _ = child_pos
    if gov_end > child_start:
        return False
    gap = testo[gov_end:child_start]
    if len(gap) > 15:
        return False
    return bool(_INTERPOSED_RE.fullmatch(gap))


def _fallback_governor(
    child: EventoGrezzo,
    events: list[EventoGrezzo],
    testo: str,
) -> EventoGrezzo | None:
    for other in events:
        if other.indice == child.indice:
            continue
        if other.frase_indice != child.frase_indice:
            continue
        if not _is_viable_governor(other):
            continue
        lemma = _lemma_norm(other.lemma)
        classe = other.classe_verbo_reggente or "nessuna"
        if not (
            lemma in _ASPECTUAL_LEMMAS
            or classe != "nessuna"
            or (other.modalita and other.modalita != "fattuale")
        ):
            continue
        if testo and not _interposed_ok(testo, other, child):
            continue
        if not testo:
            continue
        return other
    return None


def find_governor(
    child: EventoGrezzo,
    events: list[EventoGrezzo],
    testo: str = "",
) -> EventoGrezzo | None:
    by_index = {event.indice: event for event in events}
    if child.completiva_di is not None:
        parent = by_index.get(child.completiva_di)
        if (
            parent is not None
            and parent.indice != child.indice
            and parent.frase_indice == child.frase_indice
            and _is_viable_governor(parent)
        ):
            return parent
    return _fallback_governor(child, events, testo)


def _sogg_args(event: EventoGrezzo) -> list[ArgomentoGrezzo]:
    return [arg for arg in event.argomenti if arg.ruolo == "SOGG"]


def _inherited_modalita(governor: EventoGrezzo, child: EventoGrezzo) -> str:
    lemma = _lemma_norm(governor.lemma)
    past = governor.tempo in _PAST_TEMPI
    condizionale = _is_condizionale(governor)
    if governor.modalita == "volitivo" or lemma in _VOLITIVE_LEMMAS:
        return "volitivo"
    deontico_or_potere = governor.modalita == "deontico" or lemma == "potere"
    if deontico_or_potere:
        if past and not condizionale:
            return "fattuale"
        return "deontico"
    return child.modalita or "fattuale"


def _forma_verbale(event: EventoGrezzo) -> str:
    if event.segmentazione == "gerundiva":
        return "gerundio"
    if event.segmentazione == "participiale":
        return "participio"
    return "infinito"


def _relazione_for_non_finite(
    event: EventoGrezzo,
    archi: list[ArcoEventoGrezzo],
) -> RelazioneSegnale:
    for arco in archi:
        if arco.da_indice == event.indice or arco.a_indice == event.indice:
            if arco.relazione_segnale and arco.relazione_segnale != "nessuno":
                return arco.relazione_segnale
    if event.segmentazione == "gerundiva":
        return "gerundio"
    if event.segmentazione == "participiale":
        return "participio_assoluto"
    if event.finale:
        return "scopo"
    return "nessuno"


def _shared_argomento(event: EventoGrezzo, governo: EventoGrezzo | None) -> str | None:
    own = {
        (arg.forma or "").strip()
        for arg in event.argomenti
        if arg.ruolo in ("SOGG", "OGG") and (arg.forma or "").strip()
    }
    if governo is None:
        return next(iter(own), None)
    other = {
        (arg.forma or "").strip()
        for arg in governo.argomenti
        if arg.ruolo in ("SOGG", "OGG") and (arg.forma or "").strip()
    }
    shared = own & other
    if shared:
        return next(iter(shared))
    return next(iter(own), None)


def _predicati_key(item: PredicatoNonFinito) -> tuple[str, str]:
    return (_lemma_norm(item.lemma), (item.span or "").strip())


def _rebuild_sheet(
    factsheet: ChunkFactsheet | FraseFactsheet,
    *,
    eventi: list[EventoGrezzo] | None = None,
    archi: list[ArcoEventoGrezzo] | None = None,
    quarantena: list[FrammentoQuarantena] | None = None,
    predicati_non_finiti: list[PredicatoNonFinito] | None = None,
) -> ChunkFactsheet | FraseFactsheet:
    return type(factsheet)(
        eventi=list(factsheet.eventi) if eventi is None else eventi,
        archi=list(factsheet.archi) if archi is None else archi,
        quarantena=list(factsheet.quarantena) if quarantena is None else quarantena,
        predicati_non_finiti=(
            list(getattr(factsheet, "predicati_non_finiti", []) or [])
            if predicati_non_finiti is None
            else predicati_non_finiti
        ),
    )


def _polish_finite_event(event: EventoGrezzo) -> EventoGrezzo:
    """Dictionary infinitive + realized deontic/potere in the past (A2 on LLM-collapsed rows)."""
    lemma = _normalize_it_lemma(event.lemma)
    modalita = event.modalita or "fattuale"
    if (
        modalita == "deontico"
        and event.tempo in _PAST_TEMPI
        and not _is_condizionale(event)
        and _lemma_norm(lemma) not in _DEONTIC_LEMMAS
    ):
        modalita = "fattuale"
    updates: dict[str, object] = {}
    if lemma != event.lemma:
        updates["lemma"] = lemma
    if modalita != (event.modalita or "fattuale"):
        updates["modalita"] = modalita
    if modalita == "fattuale":
        updates["modalizzato"] = False
        if event.segmentazione in _MAIN_FINITE_SEGS:
            # Coordinate/main finite assertions are not purpose-complements of
            # a previous verb (LLM tags "si tolse" as finale/completiva).
            updates["completiva_di"] = None
            updates["finale"] = False
            updates["classe_verbo_reggente"] = "nessuna"
    elif event.modalizzato != (modalita != "fattuale"):
        updates["modalizzato"] = modalita != "fattuale"
    return event.model_copy(update=updates) if updates else event


def _collapse_child(child: EventoGrezzo, governor: EventoGrezzo) -> EventoGrezzo:
    lemma = _normalize_it_lemma(child.lemma)
    args = list(child.argomenti)
    if not _sogg_args(child):
        inherited = _sogg_args(governor)
        if inherited:
            args = list(inherited) + [arg for arg in args if arg.ruolo != "SOGG"]
    modalita = _inherited_modalita(governor, child)
    seg = (
        governor.segmentazione
        if governor.segmentazione in _FINITE_SEGS
        else "principale_finita"
    )
    return child.model_copy(
        update={
            "lemma": lemma,
            "tempo": governor.tempo,
            "frase_tipo": governor.frase_tipo,
            "segmentazione": seg,
            "completiva_di": None,
            "argomenti": args,
            "modalita": modalita,
            "modalizzato": modalita != "fattuale",
        }
    )


def _finite_gate(
    factsheet: ChunkFactsheet | FraseFactsheet,
    testo: str = "",
) -> ChunkFactsheet | FraseFactsheet:
    """A1+A2+A3: only finite verbs become event nodes. Idempotent."""
    events = list(factsheet.eventi)
    if not events and not getattr(factsheet, "predicati_non_finiti", None):
        return factsheet

    extra = list(factsheet.quarantena)
    predicati = list(getattr(factsheet, "predicati_non_finiti", []) or [])
    seen_pred = {_predicati_key(item) for item in predicati}
    drop_spurious: set[int] = set()
    remaining: list[EventoGrezzo] = []

    for event in events:
        if _lemma_norm(event.lemma) == "essere":
            span_tokens = {
                tok.casefold()
                for tok in re.findall(r"[A-Za-zÀ-ÿ']+", event.span or "")
            }
            if span_tokens and span_tokens.isdisjoint(_ESSERE_FORMS):
                drop_spurious.add(event.indice)
                extra.append(
                    FrammentoQuarantena(
                        frammento=event.span or event.lemma,
                        motivo="evento spurio",
                        span=event.span or "",
                    )
                )
                continue
        if _is_adverb_lemma(event.lemma):
            drop_spurious.add(event.indice)
            extra.append(
                FrammentoQuarantena(
                    frammento=event.span or event.lemma,
                    motivo="evento spurio",
                    span=event.span or "",
                )
            )
            continue
        if testo and _span_inside_quotes(testo, event.span or ""):
            drop_spurious.add(event.indice)
            extra.append(
                FrammentoQuarantena(
                    frammento=event.span or event.lemma,
                    motivo="evento spurio",
                    span=event.span or "",
                )
            )
            continue
        remaining.append(event)

    if not remaining:
        kept_archi = [
            arco
            for arco in factsheet.archi
            if arco.da_indice not in drop_spurious and arco.a_indice not in drop_spurious
        ]
        return _rebuild_sheet(
            factsheet,
            eventi=[],
            archi=kept_archi,
            quarantena=extra,
            predicati_non_finiti=predicati,
        )

    by_index = {event.indice: event for event in remaining}
    non_finite = [event for event in remaining if _is_non_finite_event(event)]
    finite = [event for event in remaining if not _is_non_finite_event(event)]

    gov_to_children: dict[int, list[EventoGrezzo]] = defaultdict(list)
    ungoverned: list[EventoGrezzo] = []
    for child in non_finite:
        governor = find_governor(child, remaining, testo)
        if governor is not None and _should_collapse_governor(governor):
            gov_to_children[governor.indice].append(child)
        else:
            ungoverned.append(child)

    absorbed: set[int] = set()
    promoted: list[EventoGrezzo] = []
    retarget: dict[int, int] = {}
    for gov_idx, children in gov_to_children.items():
        governor = by_index[gov_idx]
        absorbed.add(gov_idx)
        collapsed = [_collapse_child(child, governor) for child in children]
        if governor.e_testa and collapsed:
            collapsed[0] = collapsed[0].model_copy(update={"e_testa": True})
        for child, new_event in zip(children, collapsed, strict=False):
            retarget.setdefault(gov_idx, new_event.indice)
            promoted.append(new_event)
        child_lemma = _lemma_norm(collapsed[0].lemma) if collapsed else _lemma_norm(governor.lemma)
        extra.append(
            FrammentoQuarantena(
                frammento=governor.span or governor.lemma,
                motivo=f"assorbito in {child_lemma}",
                span=governor.span or "",
            )
        )

    finite_kept = [event for event in finite if event.indice not in absorbed]
    # Retarget completiva_di that pointed at an absorbed governor.
    rewritten: list[EventoGrezzo] = []
    for event in finite_kept + promoted:
        completiva = event.completiva_di
        if completiva in absorbed:
            completiva = retarget.get(completiva)
        if completiva == event.indice:
            completiva = None
        if completiva != event.completiva_di:
            event = event.model_copy(update={"completiva_di": completiva})
        rewritten.append(_polish_finite_event(event))

    e_testa = next((event for event in rewritten if event.e_testa), None)
    if e_testa is None and rewritten:
        rewritten[0] = rewritten[0].model_copy(update={"e_testa": True})
        e_testa = rewritten[0]

    for event in ungoverned:
        governo = e_testa
        same_sentence = [
            item for item in rewritten if item.frase_indice == event.frase_indice
        ]
        if same_sentence:
            governo = next((item for item in same_sentence if item.e_testa), same_sentence[0])
        pred = PredicatoNonFinito(
            lemma=event.lemma,
            span=event.span or "",
            forma_verbale=_forma_verbale(event),  # type: ignore[arg-type]
            relazione_segnale=_relazione_for_non_finite(event, list(factsheet.archi)),
            governo_indice=governo.indice if governo is not None else None,
            argomento_condiviso=_shared_argomento(event, governo),
        )
        key = _predicati_key(pred)
        if key not in seen_pred:
            seen_pred.add(key)
            predicati.append(pred)

    kept_indices = {event.indice for event in rewritten}

    def _map_idx(idx: int) -> int | None:
        if idx in retarget and idx not in kept_indices:
            return retarget[idx]
        if idx in kept_indices:
            return idx
        return None

    kept_archi: list[ArcoEventoGrezzo] = []
    for arco in factsheet.archi:
        da = _map_idx(arco.da_indice)
        a = _map_idx(arco.a_indice)
        if da is None or a is None or da == a:
            continue
        if da != arco.da_indice or a != arco.a_indice:
            arco = arco.model_copy(update={"da_indice": da, "a_indice": a})
        kept_archi.append(arco)

    return _rebuild_sheet(
        factsheet,
        eventi=rewritten,
        archi=kept_archi,
        quarantena=extra,
        predicati_non_finiti=predicati,
    )


def _prune_spurious_events(
    factsheet: ChunkFactsheet | FraseFactsheet,
    testo: str = "",
) -> ChunkFactsheet | FraseFactsheet:
    """Compat wrapper: the finite gate replaced the old prune (A6)."""
    return _finite_gate(factsheet, testo)


def _looks_non_referential(forma: str) -> bool:
    """A SOGG/OGG filler must name a participant, not be a clause or a predicate."""
    text = (forma or "").strip()
    if not text:
        return False
    if text[0] in _QUOTE_OPENERS:
        return True
    if any(ch in _SENTENCE_MARKS for ch in text):
        return True
    return len(text.split()) > 12


def evaluate_checklist(
    factsheet: ChunkFactsheet | FraseFactsheet,
    testo: str = "",
) -> list[ChecklistViolation]:
    violations: list[ChecklistViolation] = []
    counts: dict[int, int] = {}
    for event in factsheet.eventi:
        counts[event.indice] = counts.get(event.indice, 0) + 1
    duplicates = {indice for indice, count in counts.items() if count > 1}
    for event in factsheet.eventi:
        if event.indice in duplicates:
            violations.append(
                ChecklistViolation(
                    kind="evento",
                    indice=event.indice,
                    motivo="indice duplicato",
                )
            )

    index_set = {event.indice for event in factsheet.eventi}
    by_index: dict[int, EventoGrezzo] = {}
    for event in factsheet.eventi:
        by_index[event.indice] = event

    pool = list(factsheet.eventi)
    for event in factsheet.eventi:
        has_sogg = any(arg.ruolo == "SOGG" for arg in event.argomenti)
        if not has_sogg and event.sogg_speciale == "nessuno":
            violations.append(
                ChecklistViolation(
                    kind="evento",
                    indice=event.indice,
                    motivo="soggetto mancante",
                )
            )
        if any(
            arg.ruolo in ("SOGG", "OGG") and _looks_non_referential(arg.forma)
            for arg in event.argomenti
        ):
            violations.append(
                ChecklistViolation(
                    kind="evento",
                    indice=event.indice,
                    motivo="soggetto non referenziale",
                )
            )
        if _is_adverb_lemma(event.lemma):
            violations.append(
                ChecklistViolation(
                    kind="evento",
                    indice=event.indice,
                    motivo="evento spurio",
                )
            )
        elif _is_non_finite_event(event):
            governor = find_governor(event, pool, testo)
            if governor is None or not _should_collapse_governor(governor):
                violations.append(
                    ChecklistViolation(
                        kind="evento",
                        indice=event.indice,
                        motivo="predicato non finito",
                    )
                )
        elif _needs_italian_infinitive(event.lemma, testo) and not _is_italian_infinitive(
            event.lemma
        ):
            violations.append(
                ChecklistViolation(
                    kind="evento",
                    indice=event.indice,
                    motivo="lemma non infinito",
                )
            )

    for arco in factsheet.archi:
        if arco.da_indice not in index_set or arco.a_indice not in index_set:
            violations.append(
                ChecklistViolation(
                    kind="arco",
                    da_indice=arco.da_indice,
                    a_indice=arco.a_indice,
                    motivo="indice arco inesistente",
                )
            )
            continue
        if not _nesting_ok(by_index.get(arco.da_indice), by_index.get(arco.a_indice)):
            violations.append(
                ChecklistViolation(
                    kind="arco",
                    da_indice=arco.da_indice,
                    a_indice=arco.a_indice,
                    motivo="annidamento",
                )
            )
    return violations


def _violation_lines(violations: list[ChecklistViolation]) -> list[str]:
    lines: list[str] = []
    for item in violations:
        if item.kind == "evento":
            extra = ""
            if item.motivo == "lemma non infinito":
                extra = " — lemma = infinito di dizionario"
            elif item.motivo == "predicato non finito":
                extra = " — spostare in predicati_non_finiti"
            lines.append(f"evento indice={item.indice}: {item.motivo}{extra}")
        else:
            lines.append(
                f"arco {item.da_indice}->{item.a_indice}: {item.motivo}"
            )
    return lines


def frammento_to_item(
    frammento: FrammentoQuarantena, chunk: PeriodChunk
) -> QuarantenaItem:
    return QuarantenaItem(
        id=quarantena_id(chunk.doc_id, chunk.testo, frammento.span, frammento.motivo),
        frammento=frammento.frammento,
        motivo=frammento.motivo,
        ancora_doc=chunk.doc_id,
        ancora_chunk=chunk.id,
        ancora_span=frammento.span,
        versione_regole=RULESET_VERSION,
    )


def _failed_frammento(text: str) -> FrammentoQuarantena:
    return FrammentoQuarantena(
        frammento=text,
        motivo="estrazione fallita",
        span=text,
    )


def _apply_remaining_violations(
    factsheet: ChunkFactsheet | FraseFactsheet,
    violations: list[ChecklistViolation],
) -> ChunkFactsheet | FraseFactsheet:
    drop_event = {
        item.indice
        for item in violations
        if item.kind == "evento" and item.motivo != "soggetto non referenziale"
    }
    strip_sogg = {
        item.indice
        for item in violations
        if item.kind == "evento" and item.motivo == "soggetto non referenziale"
    }
    drop_arc = {
        (item.da_indice, item.a_indice)
        for item in violations
        if item.kind == "arco"
    }
    extra: list[FrammentoQuarantena] = []
    kept_eventi: list[EventoGrezzo] = []
    for event in factsheet.eventi:
        if event.indice in drop_event:
            motivo = next(
                item.motivo
                for item in violations
                if item.kind == "evento"
                and item.indice == event.indice
                and item.motivo != "soggetto non referenziale"
            )
            extra.append(
                FrammentoQuarantena(
                    frammento=event.span or event.lemma,
                    motivo=motivo,
                    span=event.span or "",
                )
            )
            continue
        if event.indice in strip_sogg:
            cleaned = [
                arg
                for arg in event.argomenti
                if not (
                    arg.ruolo in ("SOGG", "OGG") and _looks_non_referential(arg.forma)
                )
            ]
            if cleaned != list(event.argomenti):
                event = event.model_copy(update={"argomenti": cleaned})
        kept_eventi.append(event)

    kept_archi: list[ArcoEventoGrezzo] = []
    for arco in factsheet.archi:
        if (arco.da_indice, arco.a_indice) in drop_arc:
            motivo = next(
                item.motivo
                for item in violations
                if item.kind == "arco"
                and item.da_indice == arco.da_indice
                and item.a_indice == arco.a_indice
            )
            extra.append(
                FrammentoQuarantena(
                    frammento=arco.segnale_testuale,
                    motivo=motivo,
                    span=arco.segnale_testuale,
                )
            )
        else:
            kept_archi.append(arco)

    return _rebuild_sheet(
        factsheet,
        eventi=kept_eventi,
        archi=kept_archi,
        quarantena=[*factsheet.quarantena, *extra],
    )


def _empty_frase() -> FraseFactsheet:
    return FraseFactsheet(eventi=[], archi=[], quarantena=[])


def _failed_frase(unita: UnitaTesto) -> FraseFactsheet:
    return FraseFactsheet(
        eventi=[],
        archi=[],
        quarantena=[_failed_frammento(unita.testo)],
    )


def _sync_event_fields(event: EventoGrezzo, unita: UnitaTesto) -> EventoGrezzo:
    modalita = event.modalita or "fattuale"
    return event.model_copy(
        update={
            "modalita": modalita,
            "modalizzato": modalita != "fattuale",
            "offset_inizio": unita.offset_inizio,
            "offset_fine": unita.offset_fine,
            "frase_indice": unita.indice,
        }
    )


def _finalize_frase(factsheet: FraseFactsheet, unita: UnitaTesto) -> FraseFactsheet:
    synced = FraseFactsheet(
        eventi=[_sync_event_fields(event, unita) for event in factsheet.eventi],
        archi=list(factsheet.archi),
        quarantena=list(factsheet.quarantena),
        predicati_non_finiti=list(factsheet.predicati_non_finiti),
    )
    gated = _finite_gate(synced, unita.testo)
    return FraseFactsheet.model_validate(gated.model_dump())


def _as_frase(payload: Any) -> FraseFactsheet:
    if isinstance(payload, FraseFactsheet):
        return payload
    if isinstance(payload, ChunkFactsheet):
        return FraseFactsheet(
            eventi=list(payload.eventi),
            archi=list(payload.archi),
            quarantena=list(payload.quarantena),
            predicati_non_finiti=list(payload.predicati_non_finiti),
        )
    return FraseFactsheet.model_validate(payload)


def _outcome_from_sheet(
    factsheet: ChunkFactsheet,
    chunk: PeriodChunk,
    *,
    llm_calls: int,
    reused: bool,
) -> ExtractionOutcome:
    items = [frammento_to_item(item, chunk) for item in factsheet.quarantena]
    return ExtractionOutcome(
        factsheet=factsheet,
        quarantena=items,
        llm_calls=llm_calls,
        reused=reused,
    )


def _failed_outcome(chunk: PeriodChunk, llm_calls: int) -> ExtractionOutcome:
    frammento = _failed_frammento(chunk.testo)
    factsheet = ChunkFactsheet(eventi=[], archi=[], quarantena=[frammento])
    return ExtractionOutcome(
        factsheet=factsheet,
        quarantena=[frammento_to_item(frammento, chunk)],
        llm_calls=llm_calls,
        reused=False,
    )


async def _estrai_frase_inner(
    unita: UnitaTesto,
    *,
    job_id: str | None = None,
    session: Any = None,
) -> FraseExtractionOutcome:
    if unita.tipo == "dialogo":
        return FraseExtractionOutcome(
            factsheet=_empty_frase(), llm_calls=0, reused=False
        )

    if unita.zona_id:
        async with _cache_session(session) as persist_session:
            if persist_session is not None:
                cached = await load_cached_frase(
                    persist_session, unita.zona_id, unita.indice
                )
                if cached is not None:
                    sheet = _finalize_frase(cached, unita)
                    await persist_frase(persist_session, unita, sheet)
                    return FraseExtractionOutcome(
                        factsheet=sheet, llm_calls=0, reused=True
                    )

    llm_calls = 0
    try:
        raw = await call_structured(
            SYSTEM_FRASE,
            user_frase(unita.testo, tipo=unita.tipo),
            FraseFactsheet,
            temperature=0,
            job_id=job_id,
        )
        llm_calls += 1
        factsheet = _finalize_frase(_as_frase(raw), unita)
        violations = evaluate_checklist(factsheet, testo=unita.testo)
        if violations and llm_calls < _MAX_CALLS_PER_SENTENCE:
            raw = await call_structured(
                SYSTEM_FRASE_CORREZIONE,
                user_frase_correzione(
                    unita.testo,
                    factsheet.model_dump_json(),
                    _violation_lines(violations),
                ),
                FraseFactsheet,
                temperature=0,
                job_id=job_id,
            )
            llm_calls += 1
            factsheet = _finalize_frase(_as_frase(raw), unita)
            leftover = evaluate_checklist(factsheet, testo=unita.testo)
            if leftover:
                factsheet = _apply_remaining_violations(factsheet, leftover)  # type: ignore[assignment]
                factsheet = _finalize_frase(_as_frase(factsheet), unita)
        elif violations:
            factsheet = _apply_remaining_violations(factsheet, violations)  # type: ignore[assignment]
            factsheet = _finalize_frase(_as_frase(factsheet), unita)
    except Exception:
        factsheet = _failed_frase(unita)

    async with _cache_session(session) as persist_session:
        if persist_session is not None:
            await persist_frase(persist_session, unita, factsheet)
    return FraseExtractionOutcome(
        factsheet=factsheet, llm_calls=llm_calls, reused=False
    )


async def estrai_frase(
    unita: UnitaTesto,
    *,
    job_id: str | None = None,
    session: Any = None,
) -> FraseFactsheet:
    """One structured call per narrativa sentence; dialogo → empty, zero LLM."""
    outcome = await _estrai_frase_inner(unita, job_id=job_id, session=session)
    return outcome.factsheet


async def estrai_unita_zona(
    zone_units: list[UnitaTesto],
    *,
    job_id: str | None = None,
    session: Any = None,
) -> list[FraseFactsheet]:
    sheets: list[FraseFactsheet] = []
    for unita in zone_units:
        sheets.append(await estrai_frase(unita, job_id=job_id, session=session))
    return sheets


def _merge_frasi(sheets: list[FraseFactsheet]) -> ChunkFactsheet:
    eventi: list[EventoGrezzo] = []
    archi: list[ArcoEventoGrezzo] = []
    quarantena: list[FrammentoQuarantena] = []
    predicati: list[PredicatoNonFinito] = []
    next_indice = 0
    for sheet in sheets:
        remap: dict[int, int] = {}
        for event in sheet.eventi:
            remap[event.indice] = next_indice
            next_indice += 1
        for event in sheet.eventi:
            completiva = event.completiva_di
            if completiva is not None:
                completiva = remap.get(completiva)
            eventi.append(
                event.model_copy(
                    update={
                        "indice": remap[event.indice],
                        "completiva_di": completiva,
                    }
                )
            )
        for arco in sheet.archi:
            if arco.da_indice not in remap or arco.a_indice not in remap:
                continue
            archi.append(
                arco.model_copy(
                    update={
                        "da_indice": remap[arco.da_indice],
                        "a_indice": remap[arco.a_indice],
                    }
                )
            )
        for pred in sheet.predicati_non_finiti:
            governo = pred.governo_indice
            if governo is not None:
                governo = remap.get(governo)
            predicati.append(pred.model_copy(update={"governo_indice": governo}))
        quarantena.extend(sheet.quarantena)
    return ChunkFactsheet(
        eventi=eventi,
        archi=archi,
        quarantena=quarantena,
        predicati_non_finiti=predicati,
    )


def _units_from_chunk(chunk: PeriodChunk) -> list[UnitaTesto]:
    units = preprocess_zona(
        _ZonaShim(id=chunk.id, testo=chunk.testo, offset_inizio=0)
    )
    if units:
        return units
    if not (chunk.testo or "").strip():
        return []
    return [
        UnitaTesto(
            testo=fold_text(chunk.testo),
            offset_inizio=0,
            offset_fine=len(chunk.testo),
            tipo="narrativa",
            connettivo_confine=None,
            zona_id=chunk.id,
            indice=0,
        )
    ]


async def estrai(
    chunk: PeriodChunk,
    *,
    session: Any = None,
    job_id: str | None = None,
) -> ExtractionOutcome:
    """Compatibility wrapper: per-sentence extraction merged into a ChunkFactsheet."""
    if session is not None:
        cached = await load_cached_factsheet(session, chunk.id)
        if cached is not None:
            outcome = _outcome_from_sheet(cached, chunk, llm_calls=0, reused=True)
            await persist_chunk(session, chunk, outcome.factsheet)
            return outcome

    units = _units_from_chunk(chunk)
    sheets: list[FraseFactsheet] = []
    llm_calls = 0
    try:
        for unita in units:
            inner = await _estrai_frase_inner(unita, job_id=job_id, session=session)
            llm_calls += inner.llm_calls
            sheets.append(inner.factsheet)
        factsheet = _merge_frasi(sheets)
        leftover = evaluate_checklist(factsheet, testo=chunk.testo)
        if leftover:
            factsheet = _apply_remaining_violations(factsheet, leftover)  # type: ignore[assignment]
            factsheet = ChunkFactsheet.model_validate(factsheet.model_dump())
        outcome = _outcome_from_sheet(
            factsheet, chunk, llm_calls=llm_calls, reused=False
        )
    except Exception:
        outcome = _failed_outcome(chunk, llm_calls)

    if session is not None:
        await persist_chunk(session, chunk, outcome.factsheet)
    return outcome


__all__ = [
    "ChecklistViolation",
    "ExtractionOutcome",
    "FraseExtractionOutcome",
    "Phase1Event",
    "Phase1Result",
    "Phase2Event",
    "Phase2Result",
    "estrai",
    "estrai_frase",
    "estrai_unita_zona",
    "evaluate_checklist",
    "load_cached_factsheet",
    "load_cached_frase",
    "persist_chunk",
    "persist_frase",
    "session_run",
    "_finite_gate",
]
