"""D7 / sez. 11 — temporal placement, agent B, fase 13.

Three order levels: frozen SEQUENZA, chronology (ancore), ingestion COLLEGATO.
Append-only. No DELETE. Cycles → :Quarantena. PRECEDE is not written:
that type left TipoRelazione.

Stage 5 Allen constraint network lives in ``chiusura_temporale`` / ``allen``
(M-allen). ``esegui`` no longer materializes event-to-event PRECEDE.
"""

from __future__ import annotations

import calendar
import inspect
import json
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.models.event_graph import (
    ArcoEvento,
    ArgomentoRisolto,
    EventoRisolto,
    QuarantenaItem,
    SottoGrafo,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.candidati_entita import (
    condividono_entita,
    seleziona_per_entita,
)
from app.pipeline.event_graph.chains import assegna_catena
from app.pipeline.event_graph.config import settings
from app.pipeline.event_graph.ids import content_hash, quarantena_id
from app.pipeline.event_graph.infra.llm import call_structured  # noqa: F401
from app.pipeline.event_graph.temporal_prompts import SYSTEM_TEMPORAL, user_pair

REGOLA = "temporal_placement.esegui"
Ordine = Literal["prima", "dopo", "sovrapposto", "incerto"]

_EVENT_EVENT_TIPI = frozenset(
    {
        "CAUSA",
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
_ARG_RUOLI = frozenset({"SOGG", "OGG", "OBL", "TEMPO", "LUOGO", "MODO"})
_SOGG_OGG = frozenset({"SOGG", "OGG"})

_ISO_DATETIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)$"
)
_ISO_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
_ISO_MONTH = re.compile(r"^(\d{4}-\d{2})$")
_ISO_YEAR = re.compile(r"^(\d{4})$")
# Precisione oraria: _ISO_DATETIME esige i minuti, quindi "1843-12-24T18" non
# era riconosciuto. Il livello temporale v2 la ammette (MT2).
_ISO_HOUR = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2})(Z|[+-]\d{2}:\d{2})?$")
# Quanti campi orari porta una stringa già riconosciuta da _ISO_DATETIME:
# serve solo a _bound_end, per chiudere l'ultima unità invece di restituire
# un intervallo di larghezza zero.
_ISO_HAS_SECONDS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_ISO_ANY = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
    r"|\d{4}-\d{2}-\d{2}|\d{4}-\d{2}|\d{4})"
)
_INTERVAL_DAL_AL = re.compile(
    r"(?:dal|da|from|tra(?:\s+il)?|between)\s+"
    r"(\d{4}(?:-\d{2}(?:-\d{2})?)?)"
    r"\s+(?:al|a|fino\s+al|to|e(?:\s+il)?|and)\s+"
    r"(\d{4}(?:-\d{2}(?:-\d{2})?)?)",
    re.IGNORECASE,
)
_INTERVAL_DASH_YEARS = re.compile(r"^(\d{4})\s*[-–—]\s*(\d{4})$")
_RELATIVE_RE = re.compile(
    r"(giorno dopo|giorno prima|day after|day before|next day|"
    r"following day|l['\u2019]indomani|anno dopo|anno prima|"
    r"year later|year before|following year|mese dopo|mese prima|"
    r"days later|days after|giorni dopo|giorni prima)",
    re.IGNORECASE,
)

_PERSISTED_QUERY = (
    "MATCH (e:Evento) "
    "WHERE e.fuso_in IS NULL OR e.fuso_in = '' "
    "OPTIONAL MATCH (e)-[rel:SOGG|OGG|OBL|TEMPO|LUOGO|MODO]->(m:Menzione) "
    "WITH e, collect(DISTINCT {ruolo: type(rel), menzione_id: m.id}) AS argomenti "
    "RETURN e.id AS id, e.lemma AS lemma, e.tempo AS tempo, "
    "e.tempo_assoluto AS tempo_assoluto, "
    "e.tempo_assoluto_grezzo AS tempo_assoluto_grezzo, "
    "e.posizione_doc AS posizione_doc, e.posizione_chunk AS posizione_chunk, "
    "e.fuso_in AS fuso_in, e.piano AS piano, e.span AS span, "
    "e.documento AS documento, e.chunk_id AS chunk_id, "
    "argomenti AS argomenti"
)


class TemporalOrder(BaseModel):
    ordine: Literal["prima", "dopo", "sovrapposto", "incerto"]
    tempo_assoluto: str | dict[str, Any] | None = None


@dataclass
class TemporalOutcome:
    archi_aggiunti: list[ArcoEvento] = field(default_factory=list)
    archi_superati: list[tuple[ArcoEvento, str]] = field(default_factory=list)
    quarantena: list[QuarantenaItem] = field(default_factory=list)
    llm_calls: int = 0


def introdurrebbe_ciclo(
    archi: Sequence[ArcoEvento],
    da_id: str,
    a_id: str,
) -> list[str] | None:
    """Path of active PRECEDE if adding da→a would cycle; else None."""
    if not da_id or not a_id:
        return None
    if da_id == a_id:
        return [da_id, a_id]
    graph: dict[str, list[str]] = defaultdict(list)
    for arco in archi:
        if str(arco.tipo) != "PRECEDE":
            continue
        if arco.props.get("superato_da"):
            continue
        graph[arco.da_id].append(arco.a_id)
    path = _path_ids_precede(graph, a_id, da_id)
    if path is None:
        return None
    return [da_id] + path


def _path_ids_precede(
    graph: dict[str, list[str]], start: str, goal: str
) -> list[str] | None:
    if start == goal:
        return [start]
    seen = {start}
    stack: list[tuple[str, list[str]]] = [(start, [start])]
    while stack:
        cur, path = stack.pop()
        for nxt in graph.get(cur, ()):
            if nxt == goal:
                return path + [nxt]
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, path + [nxt]))
    return None


def normalizza_ancora(grezzo: str | None) -> str | dict[str, Any] | None:
    """Parse tempo_assoluto_grezzo → ISO / interval / symbolic. No LLM."""
    if grezzo is None:
        return None
    text = str(grezzo).strip()
    if not text:
        return None

    interval = _parse_interval(text)
    if interval is not None:
        return interval

    if _RELATIVE_RE.search(text):
        return {
            "relativo_a": None,
            "offset": text,
            "origine": "grezzo",
        }

    iso = _parse_iso_token(text)
    if iso is not None:
        return iso

    extracted = _extract_single_iso(text)
    if extracted is not None:
        return extracted
    return None


def confronta(a: Any, b: Any) -> Ordine:
    """Compare two absolute anchors. Interval expansion per sez. 11."""
    left = _expand_bounds(_tempo_of(a))
    right = _expand_bounds(_tempo_of(b))
    if left is None or right is None:
        return "incerto"
    a_start, a_end = left
    b_start, b_end = right
    if a_end < b_start:
        return "prima"
    if a_start > b_end:
        return "dopo"
    return "sovrapposto"


def seleziona_candidati(
    nuovo: EventoRisolto,
    persistenti: Sequence[EventoRisolto],
    *,
    max_n: int,
) -> list[EventoRisolto]:
    """Priority (a) shared mention (b) nearby time (c) textual reference, then cap."""
    pool = [
        event
        for event in persistenti
        if event.id != nuovo.id and not event.fuso_in
    ]
    seen: set[str] = set()
    ranked: list[EventoRisolto] = []

    def _take(predicate) -> None:
        for event in pool:
            key = event.id or str(id(event))
            if key in seen:
                continue
            if predicate(event):
                seen.add(key)
                ranked.append(event)

    for event in seleziona_per_entita(nuovo, pool):
        key = event.id or str(id(event))
        if key in seen:
            continue
        if not condividono_entita(nuovo, event):
            continue
        seen.add(key)
        ranked.append(event)
    _take(lambda other: _nearby_time(nuovo, other))
    _take(lambda other: _riferimento_testuale(nuovo, other))
    return ranked[: max(0, int(max_n))]


def placeholder_target(
    nuovo: EventoRisolto,
    persistenti: Sequence[EventoRisolto],
) -> EventoRisolto | None:
    """Nearest previous event by (posizione_doc, posizione_chunk, id). ONE."""
    pool = [
        event
        for event in persistenti
        if event.id != nuovo.id and not event.fuso_in
    ]
    nuovo_key = _pos_key(nuovo)
    previous = [event for event in pool if _pos_key(event) < nuovo_key]
    if not previous:
        return None
    return max(previous, key=_pos_key)


async def esegui(
    session: Any,
    sotto: SottoGrafo,
    job_id: str,
    *,
    call_structured: Any = None,  # noqa: F811
) -> TemporalOutcome:
    """Phase-13 temporal placement. Mutates sotto.archi / sotto.quarantena."""
    llm_fn = (
        call_structured
        if call_structured is not None
        else globals()["call_structured"]
    )
    outcome = TemporalOutcome()
    persistenti = await _carica_persistenti(session, sotto)
    pool = _pool_eventi(sotto, persistenti)
    ordine_cache: dict[tuple[str, str], Ordine] = {}
    max_n = int(settings.EVENT_GRAPH_TEMPORAL_MAX_CANDIDATES)

    nuovi = [event for event in sotto.eventi if event.id and not event.fuso_in]
    nuovi.sort(key=_pos_key)

    for nuovo in nuovi:
        outcome.llm_calls += await _ancora_evento(
            nuovo, pool, llm_fn, job_id, ordine_cache
        )

    incerto_nuovi: set[str] = set()
    for nuovo in nuovi:
        candidati = seleziona_candidati(nuovo, pool, max_n=max_n)
        for candidato in candidati:
            ordine, calls = await _ordine_coppia(
                nuovo, candidato, llm_fn, job_id, ordine_cache
            )
            outcome.llm_calls += calls
            if ordine in ("prima", "dopo"):
                earlier, later = (
                    (nuovo, candidato) if ordine == "prima" else (candidato, nuovo)
                )
                base = _base_per_coppia(nuovo, candidato)
                _scrivi_precede(
                    sotto,
                    session,
                    job_id,
                    earlier,
                    later,
                    base,
                    outcome,
                )
            elif ordine == "incerto":
                if not _collegati(sotto.archi, nuovo.id, candidato.id):
                    incerto_nuovi.add(nuovo.id)
            _forse_aggiorna(sotto, session, job_id, nuovo, candidato, outcome)

        if nuovo.id in incerto_nuovi:
            _scrivi_placeholder(sotto, session, job_id, nuovo, pool, outcome)

    _affina_retroattivo(sotto, session, job_id, nuovi, pool, outcome)
    if session is not None:
        await _flush_session(session)
    return outcome


def _tempo_of(value: Any) -> str | dict[str, Any] | None:
    if isinstance(value, EventoRisolto):
        return value.tempo_assoluto
    if isinstance(value, (str, dict)) or value is None:
        return value
    return None


def _parse_interval(text: str) -> dict[str, str] | None:
    dashed = _INTERVAL_DASH_YEARS.match(text.strip())
    if dashed:
        start, end = dashed.group(1), dashed.group(2)
        if start != end:
            return {"da": start, "a": end}
    match = _INTERVAL_DAL_AL.search(text)
    if match:
        return {"da": match.group(1), "a": match.group(2)}
    return None


def _parse_iso_token(text: str) -> str | None:
    token = text.strip()
    if (
        _ISO_DATETIME.match(token)
        or _ISO_DATE.match(token)
        or _ISO_MONTH.match(token)
        or _ISO_YEAR.match(token)
    ):
        return token
    return None


def _extract_single_iso(text: str) -> str | None:
    found = _ISO_ANY.findall(text)
    if len(found) != 1:
        return None
    token = found[0]
    if _ISO_DATETIME.match(token) or _ISO_DATE.match(token) or _ISO_MONTH.match(token):
        return token
    if _ISO_YEAR.match(token):
        return token
    return None


def _is_dated(value: Any) -> bool:
    return _expand_bounds(_tempo_of(value)) is not None


def _is_symbolic(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if "da" in value and "a" in value and _expand_bounds(value) is not None:
        return False
    return "relativo_a" in value or "offset" in value


def _expand_bounds(value: Any) -> tuple[datetime, datetime] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        if "da" in value and "a" in value:
            start = _bound_start(value.get("da"))
            end = _bound_end(value.get("a"))
            if start is None or end is None:
                return None
            return start, end
        return None
    if not isinstance(value, str):
        return None
    token = value.strip()
    start = _bound_start(token)
    end = _bound_end(token)
    if start is None or end is None:
        return None
    return start, end


def _bound_start(token: Any) -> datetime | None:
    if not isinstance(token, str):
        return None
    text = token.strip()
    parsed = _parse_datetime(text)
    if parsed is not None:
        return parsed
    hour = _parse_hour(text)
    if hour is not None:
        return hour
    try:
        if _ISO_DATE.match(text):
            return datetime.fromisoformat(text)
        if _ISO_MONTH.match(text):
            year, month = int(text[:4]), int(text[5:7])
            return datetime(year, month, 1)
        if _ISO_YEAR.match(text):
            return datetime(int(text), 1, 1)
    except ValueError:
        # "1843-13-45" passa la regex ma non il calendario: il livello
        # temporale è best-effort, non collocabile ≠ ingestione interrotta.
        return None
    return None


def _bound_end(token: Any) -> datetime | None:
    """Ultimo istante *incluso* nell'unità denotata dal token.

    Le tre precisioni sotto il giorno seguono la stessa regola di anno / mese /
    giorno — l'ultimo secondo intero dell'unità — invece di collassare su un
    intervallo di larghezza zero come faceva _parse_datetime da sola. Il
    secondo resta l'unità atomica, quindi per una stringa al secondo inizio e
    fine coincidono, come prima.
    """
    if not isinstance(token, str):
        return None
    text = token.strip()
    parsed = _parse_datetime(text)
    if parsed is not None:
        if _ISO_HAS_SECONDS.match(text):
            return parsed
        return parsed.replace(second=59)
    hour = _parse_hour(text)
    if hour is not None:
        return hour.replace(minute=59, second=59)
    try:
        if _ISO_DATE.match(text):
            return datetime.fromisoformat(text + "T23:59:59")
        if _ISO_MONTH.match(text):
            year, month = int(text[:4]), int(text[5:7])
            last = calendar.monthrange(year, month)[1]
            return datetime(year, month, last, 23, 59, 59)
        if _ISO_YEAR.match(text):
            return datetime(int(text), 12, 31, 23, 59, 59)
    except (ValueError, calendar.IllegalMonthError):
        return None
    return None


def _parse_datetime(text: str) -> datetime | None:
    if not _ISO_DATETIME.match(text):
        return None
    cleaned = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _parse_hour(text: str) -> datetime | None:
    """Precisione oraria, "1843-12-24T18". Il fuso è tenuto come da _parse_datetime."""
    match = _ISO_HOUR.match(text)
    if match is None:
        return None
    cleaned = match.group(1) + ":00"
    offset = match.group(2)
    if offset:
        cleaned += "+00:00" if offset in {"Z", "z"} else offset
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _values_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, dict) and isinstance(right, dict):
        return json.dumps(left, sort_keys=True, default=str) == json.dumps(
            right, sort_keys=True, default=str
        )
    return left == right


def _apply_tempo(evento: EventoRisolto, value: str | dict[str, Any]) -> bool:
    last = evento.tempo_assoluto_revisioni[-1] if evento.tempo_assoluto_revisioni else None
    if _values_equal(value, evento.tempo_assoluto) or _values_equal(value, last):
        if evento.tempo_assoluto is None:
            evento.tempo_assoluto = value
        return False
    evento.tempo_assoluto_revisioni.append(value)
    evento.tempo_assoluto = value
    return True


def _sogg_ogg_ids(evento: EventoRisolto) -> set[str]:
    return {
        arg.menzione_id
        for arg in evento.argomenti
        if arg.ruolo in _SOGG_OGG and arg.menzione_id
    }


def _arg_key(arg: ArgomentoRisolto) -> tuple:
    if arg.ruolo == "OBL" and arg.preposizione:
        return (arg.ruolo, arg.menzione_id, arg.preposizione)
    return (arg.ruolo, arg.menzione_id)


def _arg_set(evento: EventoRisolto) -> set[tuple]:
    return {_arg_key(arg) for arg in evento.argomenti if arg.ruolo in _ARG_RUOLI}


def _pos_key(evento: EventoRisolto) -> tuple[int, int, str]:
    return (
        evento.posizione_doc if evento.posizione_doc is not None else 0,
        evento.posizione_chunk if evento.posizione_chunk is not None else 0,
        evento.id or "",
    )


def _nearby_time(left: EventoRisolto, right: EventoRisolto) -> bool:
    years_l = _years_of(left.tempo_assoluto)
    years_r = _years_of(right.tempo_assoluto)
    if not years_l or not years_r:
        return False
    if years_l & years_r:
        return True
    return bool(
        years_l & {year - 1 for year in years_r}
        or years_l & {year + 1 for year in years_r}
    )


def _years_of(value: Any) -> set[int]:
    bounds = _expand_bounds(value)
    if bounds is None:
        return set()
    return set(range(bounds[0].year, bounds[1].year + 1))


def _riferimento_testuale(nuovo: EventoRisolto, other: EventoRisolto) -> bool:
    if isinstance(nuovo.tempo_assoluto, dict):
        if nuovo.tempo_assoluto.get("relativo_a") == other.id:
            return True
    blob = _testo_riferimento(nuovo).casefold()
    if other.id and other.id.casefold() in blob:
        return True
    lemma = (other.lemma or "").strip().casefold()
    if lemma and re.search(rf"(?<!\w){re.escape(lemma)}(?!\w)", blob):
        return True
    return False


def _testo_riferimento(evento: EventoRisolto) -> str:
    bits = [evento.span or "", evento.tempo_assoluto_grezzo or ""]
    if isinstance(evento.tempo_assoluto, dict):
        bits.append(str(evento.tempo_assoluto.get("relativo_a") or ""))
        bits.append(json.dumps(evento.tempo_assoluto, ensure_ascii=False))
    return " ".join(bits)


def _collegati(archi: Sequence[ArcoEvento], id_a: str, id_b: str) -> bool:
    pair = {id_a, id_b}
    for arco in archi:
        if str(arco.tipo) in _EVENT_EVENT_TIPI and {arco.da_id, arco.a_id} == pair:
            return True
    return False


def _has_chain(left: EventoRisolto, right: EventoRisolto) -> bool:
    if left.catena_precedente_id == right.id or right.catena_precedente_id == left.id:
        return True
    if left.catena_id and right.catena_id and left.catena_id == right.catena_id:
        return True
    return False


def _has_placeholder_from(archi: Sequence[ArcoEvento], nuovo_id: str) -> bool:
    for arco in archi:
        if str(arco.tipo) != "COLLEGATO" or arco.da_id != nuovo_id:
            continue
        segnale = str(arco.props.get("segnale") or "")
        if segnale.startswith("ordine_"):
            return True
    return False


def _identical_arc(
    archi: Sequence[ArcoEvento],
    tipo: str,
    da_id: str,
    a_id: str,
    extra_key: str,
    extra_val: str,
) -> bool:
    for arco in archi:
        if str(arco.tipo) != tipo or arco.da_id != da_id or arco.a_id != a_id:
            continue
        if arco.props.get(extra_key) == extra_val:
            return True
    return False


def _arco_id(tipo: str, da_id: str, a_id: str, extra: str) -> str:
    return content_hash(f"{tipo}|{da_id}|{a_id}|{extra}")


def _base_per_coppia(nuovo: EventoRisolto, candidato: EventoRisolto) -> str:
    if _is_dated(nuovo) and _is_dated(candidato):
        return "dato_esplicito"
    if _riferimento_testuale(nuovo, candidato) or _is_symbolic(nuovo.tempo_assoluto):
        return "riferimento_testuale"
    return "dato_esplicito"


async def _ancora_evento(
    evento: EventoRisolto,
    pool: Sequence[EventoRisolto],
    llm_fn: Any,
    job_id: str,
    ordine_cache: dict[tuple[str, str], Ordine],
) -> int:
    parsed = normalizza_ancora(evento.tempo_assoluto_grezzo)
    if parsed is None and evento.tempo_assoluto is not None:
        parsed = evento.tempo_assoluto
    if _is_dated(evento.tempo_assoluto) and _is_symbolic(parsed):
        parsed = evento.tempo_assoluto

    calls = 0
    if _is_symbolic(parsed) or (
        parsed is None and _RELATIVE_RE.search(evento.tempo_assoluto_grezzo or "")
    ):
        if parsed is None:
            parsed = {
                "relativo_a": None,
                "offset": (evento.tempo_assoluto_grezzo or "").strip(),
                "origine": "grezzo",
            }
        ref_event, ref_date = _trova_referente(evento, pool, parsed)
        if isinstance(parsed, dict) and ref_event is not None and not parsed.get("relativo_a"):
            parsed = {**parsed, "relativo_a": ref_event.id}
        if ref_event is not None or ref_date is not None:
            result = await _chiama_llm(
                llm_fn,
                evento,
                ref_event,
                ref_date,
                job_id,
            )
            calls += 1
            if result.tempo_assoluto is not None:
                resolved = result.tempo_assoluto
                if isinstance(resolved, str):
                    resolved = normalizza_ancora(resolved) or resolved
                parsed = resolved
            if ref_event is not None:
                ordine_cache[(evento.id, ref_event.id)] = result.ordine
    if parsed is not None:
        _apply_tempo(evento, parsed)
    return calls


def _trova_referente(
    evento: EventoRisolto,
    pool: Sequence[EventoRisolto],
    parsed: str | dict[str, Any] | None,
) -> tuple[EventoRisolto | None, str | None]:
    blob = _testo_riferimento(evento)
    relativo = None
    if isinstance(parsed, dict):
        relativo = parsed.get("relativo_a")
    if relativo:
        for other in pool:
            if other.id == relativo and other.id != evento.id:
                return other, None
    for other in pool:
        if other.id == evento.id or other.fuso_in:
            continue
        if other.id and other.id in blob:
            return other, None
    lemma_hits: list[EventoRisolto] = []
    for other in pool:
        if other.id == evento.id or other.fuso_in:
            continue
        lemma = (other.lemma or "").strip()
        if lemma and re.search(rf"(?<!\w){re.escape(lemma)}(?!\w)", blob, re.IGNORECASE):
            lemma_hits.append(other)
    if len(lemma_hits) == 1:
        return lemma_hits[0], None
    grezzo = evento.tempo_assoluto_grezzo or ""
    iso = _extract_single_iso(grezzo)
    if iso and _RELATIVE_RE.search(grezzo):
        return None, iso
    return None, None


async def _ordine_coppia(
    nuovo: EventoRisolto,
    candidato: EventoRisolto,
    llm_fn: Any,
    job_id: str,
    ordine_cache: dict[tuple[str, str], Ordine],
) -> tuple[Ordine, int]:
    cached = ordine_cache.get((nuovo.id, candidato.id))
    if cached is not None:
        return cached, 0
    if _is_dated(nuovo) and _is_dated(candidato):
        return confronta(nuovo, candidato), 0
    if _ha_segnale_relativo(nuovo, candidato):
        result = await _chiama_llm(llm_fn, nuovo, candidato, None, job_id)
        if result.tempo_assoluto is not None and not _is_dated(nuovo):
            resolved = result.tempo_assoluto
            if isinstance(resolved, str):
                resolved = normalizza_ancora(resolved) or resolved
            _apply_tempo(nuovo, resolved)
        ordine_cache[(nuovo.id, candidato.id)] = result.ordine
        return result.ordine, 1
    return "incerto", 0


def _ha_segnale_relativo(nuovo: EventoRisolto, candidato: EventoRisolto) -> bool:
    if _riferimento_testuale(nuovo, candidato):
        return True
    if _is_symbolic(nuovo.tempo_assoluto) or _is_symbolic(candidato.tempo_assoluto):
        return True
    grezzo = nuovo.tempo_assoluto_grezzo or ""
    return bool(_RELATIVE_RE.search(grezzo))


async def _chiama_llm(
    llm_fn: Any,
    nuovo: EventoRisolto,
    candidato: EventoRisolto | None,
    ref_date: str | None,
    job_id: str,
) -> TemporalOrder:
    cand_id = candidato.id if candidato is not None else (ref_date or "")
    cand_lemma = candidato.lemma if candidato is not None else ""
    cand_tempo = candidato.tempo_assoluto if candidato is not None else ref_date
    cand_grezzo = candidato.tempo_assoluto_grezzo if candidato is not None else None
    raw = llm_fn(
        SYSTEM_TEMPORAL,
        user_pair(
            nuovo.id,
            nuovo.lemma,
            nuovo.tempo_assoluto,
            nuovo.tempo_assoluto_grezzo,
            cand_id,
            cand_lemma,
            cand_tempo,
            cand_grezzo,
        ),
        TemporalOrder,
        0,
        job_id,
    )
    if inspect.isawaitable(raw):
        raw = await raw
    if isinstance(raw, TemporalOrder):
        return raw
    if isinstance(raw, BaseModel):
        return TemporalOrder.model_validate(raw.model_dump())
    if isinstance(raw, dict):
        return TemporalOrder.model_validate(raw)
    return TemporalOrder(ordine="incerto")


def _scrivi_precede(
    sotto: SottoGrafo,
    session: Any,
    job_id: str,
    earlier: EventoRisolto,
    later: EventoRisolto,
    base: str,
    outcome: TemporalOutcome,
) -> ArcoEvento | None:
    """Do not materialize PRECEDE: that type left TipoRelazione (MT-A1)."""
    if _identical_arc(sotto.archi, "PRECEDE", earlier.id, later.id, "base", base):
        existing = next(
            arco
            for arco in sotto.archi
            if str(arco.tipo) == "PRECEDE"
            and arco.da_id == earlier.id
            and arco.a_id == later.id
            and arco.props.get("base") == base
        )
        _supercedi_collegato(sotto, session, existing, outcome)
        return None
    cycle = introdurrebbe_ciclo(sotto.archi, earlier.id, later.id)
    if cycle is not None:
        frammento = f"{earlier.id}->{later.id}"
        if any(item.frammento == frammento for item in sotto.quarantena):
            return None
        item = _ciclo_item(sotto, earlier, later, cycle, job_id)
        sotto.quarantena.append(item)
        outcome.quarantena.append(item)
        return None
    return None


def _scrivi_placeholder(
    sotto: SottoGrafo,
    session: Any,
    job_id: str,
    nuovo: EventoRisolto,
    pool: Sequence[EventoRisolto],
    outcome: TemporalOutcome,
) -> None:
    if _has_placeholder_from(sotto.archi, nuovo.id):
        return
    target = placeholder_target(nuovo, pool)
    if target is None:
        return
    if _collegati(sotto.archi, nuovo.id, target.id):
        return
    if _identical_arc(
        sotto.archi, "COLLEGATO", nuovo.id, target.id, "segnale", "ordine_ingestione"
    ):
        return
    rel_id = _arco_id("COLLEGATO", nuovo.id, target.id, "ordine_ingestione")
    arco = ArcoEvento(
        tipo="COLLEGATO",
        da_id=nuovo.id,
        a_id=target.id,
        props={
            "id": rel_id,
            "segnale": "ordine_ingestione",
            "run_id": job_id,
            "regola": REGOLA,
            "versione_regole": RULESET_VERSION,
        },
    )
    sotto.archi.append(arco)
    outcome.archi_aggiunti.append(arco)
    _queue_merge(session, "COLLEGATO", arco)


def _forse_aggiorna(
    sotto: SottoGrafo,
    session: Any,
    job_id: str,
    nuovo: EventoRisolto,
    candidato: EventoRisolto,
    outcome: TemporalOutcome,
) -> None:
    if (nuovo.lemma or "").strip().casefold() != (candidato.lemma or "").strip().casefold():
        return
    if not (_sogg_ogg_ids(nuovo) & _sogg_ogg_ids(candidato)):
        return
    if _arg_set(nuovo) == _arg_set(candidato):
        return
    if _has_chain(nuovo, candidato):
        return
    old, new = (
        (candidato, nuovo)
        if _pos_key(candidato) <= _pos_key(nuovo)
        else (nuovo, candidato)
    )
    if new.catena_ruolo == "AGGIORNA" and new.catena_precedente_id == old.id:
        return
    assegna_catena(new, old, "AGGIORNA")
    _queue_set_catena(session, old, new)


def _supercedi_collegato(
    sotto: SottoGrafo,
    session: Any,
    precede: ArcoEvento,
    outcome: TemporalOutcome,
) -> None:
    pair = {precede.da_id, precede.a_id}
    precede_id = str(precede.props.get("id") or "")
    if not precede_id:
        return
    for arco in sotto.archi:
        if str(arco.tipo) != "COLLEGATO":
            continue
        if {arco.da_id, arco.a_id} != pair:
            continue
        segnale = str(arco.props.get("segnale") or "")
        if not segnale.startswith("ordine_"):
            continue
        if arco.props.get("superato_da") == precede_id:
            continue
        arco.props["superato_da"] = precede_id
        if arco.da_id != precede.da_id or arco.a_id != precede.a_id:
            arco.props["conflitto"] = True
        outcome.archi_superati.append((arco, precede_id))
        _queue_set_superato(session, arco, precede_id)


def _affina_retroattivo(
    sotto: SottoGrafo,
    session: Any,
    job_id: str,
    nuovi: Sequence[EventoRisolto],
    pool: Sequence[EventoRisolto],
    outcome: TemporalOutcome,
) -> None:
    nuovi_ids = {event.id for event in nuovi}
    for nuovo in nuovi:
        if not _is_dated(nuovo):
            continue
        neighbors = [
            event
            for event in pool
            if event.id != nuovo.id
            and not event.fuso_in
            and (
                condividono_entita(nuovo, event)
                or _nearby_time(nuovo, event)
            )
        ]
        dated = [event for event in neighbors if _is_dated(event)]
        for other in dated:
            ordine = confronta(nuovo, other)
            if ordine not in ("prima", "dopo"):
                continue
            earlier, later = (nuovo, other) if ordine == "prima" else (other, nuovo)
            _scrivi_precede(
                sotto, session, job_id, earlier, later, "dato_esplicito", outcome
            )
        olds = [event for event in dated if event.id not in nuovi_ids]
        for i, left in enumerate(olds):
            for right in olds[i + 1 :]:
                ordine = confronta(left, right)
                if ordine not in ("prima", "dopo"):
                    continue
                earlier, later = (left, right) if ordine == "prima" else (right, left)
                _scrivi_precede(
                    sotto, session, job_id, earlier, later, "dato_esplicito", outcome
                )

    for arco in list(sotto.archi):
        if str(arco.tipo) != "COLLEGATO":
            continue
        segnale = str(arco.props.get("segnale") or "")
        if not segnale.startswith("ordine_"):
            continue
        if arco.props.get("superato_da"):
            continue
        by_id = {event.id: event for event in pool}
        left, right = by_id.get(arco.da_id), by_id.get(arco.a_id)
        if left is None or right is None:
            continue
        if not _is_dated(left) or not _is_dated(right):
            continue
        ordine = confronta(left, right)
        if ordine not in ("prima", "dopo"):
            continue
        earlier, later = (left, right) if ordine == "prima" else (right, left)
        _scrivi_precede(
            sotto, session, job_id, earlier, later, "dato_esplicito", outcome
        )


def _ciclo_item(
    sotto: SottoGrafo,
    earlier: EventoRisolto,
    later: EventoRisolto,
    cycle: list[str],
    job_id: str,
) -> QuarantenaItem:
    path = " → ".join(cycle)
    motivo = f"ciclo cronologico: {path}"
    doc = earlier.documento or later.documento or ""
    span = f"{earlier.id}->{later.id}"
    return QuarantenaItem(
        id=quarantena_id(doc, job_id, span, motivo),
        frammento=span,
        motivo=motivo,
        ancora_doc=doc or None,
        ancora_chunk=earlier.chunk_id or later.chunk_id,
        ancora_span=span,
        versione_regole=RULESET_VERSION,
    )


def _pool_eventi(
    sotto: SottoGrafo, persistenti: Sequence[EventoRisolto]
) -> list[EventoRisolto]:
    by_id: dict[str, EventoRisolto] = {}
    for event in persistenti:
        if event.id:
            by_id[event.id] = event
    for event in sotto.eventi:
        if event.id:
            by_id[event.id] = event
    return list(by_id.values())


async def _carica_persistenti(session: Any, sotto: SottoGrafo) -> list[EventoRisolto]:
    if session is None:
        return []
    rows = await _session_rows(session, _PERSISTED_QUERY, {})
    sotto_ids = {event.id for event in sotto.eventi if event.id}
    out: list[EventoRisolto] = []
    for row in rows or []:
        parsed = _evento_from_row(row)
        if parsed is None or parsed.fuso_in:
            continue
        if parsed.id in sotto_ids:
            continue
        out.append(parsed)
    return out


def _evento_from_row(row: Any) -> EventoRisolto | None:
    if isinstance(row, EventoRisolto):
        return row
    if not isinstance(row, dict):
        return None
    if isinstance(row.get("evento"), EventoRisolto):
        return row["evento"]
    data = dict(row)
    nested = data.get("e")
    if isinstance(nested, EventoRisolto):
        return nested
    if isinstance(nested, dict):
        merged = dict(nested)
        for key, value in data.items():
            if key != "e" and key not in merged:
                merged[key] = value
        data = merged
    eid = data.get("id")
    if eid is None:
        eid = data.get("e.id")
    if eid is None:
        return None
    args: list[ArgomentoRisolto] = []
    raw_args = data.get("argomenti") or []
    if isinstance(raw_args, list):
        for item in raw_args:
            if isinstance(item, ArgomentoRisolto):
                if item.menzione_id:
                    args.append(item)
                continue
            if not isinstance(item, dict):
                continue
            ruolo = item.get("ruolo")
            mid = item.get("menzione_id")
            if not ruolo or not mid:
                continue
            args.append(
                ArgomentoRisolto(
                    ruolo=ruolo,
                    menzione_id=str(mid),
                    preposizione=item.get("preposizione"),
                )
            )
    tempo_abs = _coerce_tempo(data.get("tempo_assoluto"))
    return EventoRisolto(
        id=str(eid),
        lemma=str(data.get("lemma") or ""),
        tempo=data.get("tempo"),
        piano=data.get("piano"),
        documento=data.get("documento"),
        chunk_id=data.get("chunk_id"),
        posizione_doc=data.get("posizione_doc"),
        posizione_chunk=data.get("posizione_chunk"),
        fuso_in=data.get("fuso_in") or None,
        span=data.get("span"),
        tempo_assoluto=tempo_abs,
        tempo_assoluto_grezzo=data.get("tempo_assoluto_grezzo"),
        argomenti=args,
    )


def _coerce_tempo(value: Any) -> str | dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                loaded = json.loads(stripped)
            except json.JSONDecodeError:
                return value
            if isinstance(loaded, dict):
                return loaded
        return value
    return None


def _queue_set_catena(
    session: Any, old: EventoRisolto, new: EventoRisolto
) -> None:
    if session is None:
        return
    query = (
        "MATCH (old:Evento {id: $old_id}) "
        "MATCH (new:Evento {id: $new_id}) "
        "SET new.catena_id = coalesce(old.catena_id, $catena_id), "
        "new.catena_ruolo = $ruolo, "
        "new.catena_precedente_id = old.id, "
        "new.catena_divergenze = $divergenze, "
        "old.catena_id = coalesce(old.catena_id, $catena_id)"
    )
    params = {
        "old_id": old.id,
        "new_id": new.id,
        "catena_id": new.catena_id,
        "ruolo": new.catena_ruolo,
        "divergenze": json.dumps(list(new.catena_divergenze or []), ensure_ascii=False),
    }
    session._pending_writes = getattr(session, "_pending_writes", [])
    session._pending_writes.append((query, params))


def _queue_merge(session: Any, tipo: str, arco: ArcoEvento) -> None:
    if session is None:
        return
    extra_key = "base" if tipo == "PRECEDE" else "segnale" if tipo == "COLLEGATO" else None
    extra_val = arco.props.get(extra_key) if extra_key else None
    set_extra = ""
    params: dict[str, Any] = {
        "da_id": arco.da_id,
        "a_id": arco.a_id,
        "rel_id": arco.props.get("id"),
        "run_id": arco.props.get("run_id"),
        "regola": arco.props.get("regola"),
        "versione_regole": arco.props.get("versione_regole"),
    }
    if extra_key:
        set_extra = f", r.{extra_key} = ${extra_key}"
        params[extra_key] = extra_val
    query = (
        f"MATCH (da:Evento {{id: $da_id}}) "
        f"MATCH (a:Evento {{id: $a_id}}) "
        f"MERGE (da)-[r:{tipo} {{id: $rel_id}}]->(a) "
        f"SET r.run_id = $run_id, r.regola = $regola, "
        f"r.versione_regole = $versione_regole{set_extra}"
    )
    session._pending_writes = getattr(session, "_pending_writes", [])
    session._pending_writes.append((query, params))


def _queue_set_superato(session: Any, arco: ArcoEvento, precede_id: str) -> None:
    if session is None:
        return
    query = (
        "MATCH (da:Evento {id: $da_id})-[r:COLLEGATO]->(a:Evento {id: $a_id}) "
        "SET r.superato_da = $superato_da, r.conflitto = $conflitto"
    )
    params = {
        "da_id": arco.da_id,
        "a_id": arco.a_id,
        "superato_da": precede_id,
        "conflitto": bool(arco.props.get("conflitto")),
    }
    session._pending_writes = getattr(session, "_pending_writes", [])
    session._pending_writes.append((query, params))


async def _flush_session(session: Any) -> None:
    pending = getattr(session, "_pending_writes", None)
    if not pending:
        return
    session._pending_writes = []
    for query, params in pending:
        await _session_rows(session, query, params)


async def _session_rows(session: Any, query: str, params: dict[str, Any]) -> list[Any]:
    raw = session.run(query, params)
    if inspect.isawaitable(raw):
        raw = await raw
    if hasattr(raw, "data"):
        rows = raw.data()
        if inspect.isawaitable(rows):
            rows = await rows
        return list(rows or [])
    return list(raw or [])


__all__ = [
    "REGOLA",
    "TemporalOrder",
    "TemporalOutcome",
    "confronta",
    "esegui",
    "introdurrebbe_ciclo",
    "normalizza_ancora",
    "placeholder_target",
    "seleziona_candidati",
]
