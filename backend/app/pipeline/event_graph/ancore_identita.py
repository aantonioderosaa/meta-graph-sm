"""MT3 — identity and normalisation of temporal anchors.

Pure functions. No LLM, no Neo4j, no I/O in the core path. Relative
expressions (ieri/oggi/domani, hours/minutes/weeks/months, past and future)
are resolved against a dated reference when one exists; without one they
stay without ``inizio``. Ancore that denote the same collocazione
are fused via ``tempo_iso.normalizza`` (or canonical etichetta when undated);
display labels are shortened and made unique within the document.

Isolation D6: ``app.pipeline.event_graph.*`` and ``app.models.event_graph``
only. Invalid input is skipped like ``tempo_iso`` (None / empty), never
swallowed with a bare exception handler.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from app.models.event_graph import (
    AncoraTemporaleProposta,
    LivelloAncoreResult,
    NaturaAncora,
    TipoAncora,
)
from app.pipeline.event_graph.ids import ancora_temporale_id
from app.pipeline.event_graph.infra.bus import publish
from app.pipeline.event_graph.tempo_iso import (
    TempoISO,
    analizza,
    chiave_ordine,
    normalizza,
    rango_granularita,
)

ETICHETTA_MAX_CHAR: Final[int] = 40
STAGE: Final[str] = "collocazione_temporale"
EVENTO: Final[str] = "ancore_identita"

CHIAVE_ISO: Final[str] = "iso"
CHIAVE_ETICHETTA: Final[str] = "etichetta"

_POS_MANCANTE: Final[int] = 10**12

_PRECISIONE_ALMENO_GIORNO: Final[frozenset[str]] = frozenset(
    {"giorno", "ora", "minuto", "secondo"}
)

_TIPO_RANGO: Final[dict[str, int]] = {
    "data": 0,
    "ora": 1,
    "scadenza": 2,
    "epoca": 3,
    "simbolica": 4,
    "relativa": 5,
}

_NATURA_RANGO: Final[dict[str, int]] = {
    "esplicita": 0,
    "intervallo": 1,
    "aperta": 2,
}

_PAROLE_NUMERO: Final[dict[str, int]] = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "due": 2,
    "tre": 3,
    "quattro": 4,
    "cinque": 5,
    "sei": 6,
    "sette": 7,
    "otto": 8,
    "nove": 9,
    "dieci": 10,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "a": 1,
    "an": 1,
}

_UNITA_CANONICA: Final[dict[str, str]] = {
    "minuto": "minuto",
    "minuti": "minuto",
    "minute": "minuto",
    "minutes": "minuto",
    "ora": "ora",
    "ore": "ora",
    "hour": "ora",
    "hours": "ora",
    "giorno": "giorno",
    "giorni": "giorno",
    "day": "giorno",
    "days": "giorno",
    "settimana": "settimana",
    "settimane": "settimana",
    "week": "settimana",
    "weeks": "settimana",
    "mese": "mese",
    "mesi": "mese",
    "month": "mese",
    "months": "mese",
}

_UNITA_PRECISIONE: Final[dict[str, str]] = {
    "minuto": "minuto",
    "ora": "ora",
    "giorno": "giorno",
    "settimana": "giorno",
    "mese": "mese",
}

_DIREZIONE_PASSATO: Final[frozenset[str]] = frozenset(
    {
        "prima",
        "before",
        "earlier",
        "ago",
        "precedente",
        "precedenti",
        "previous",
    }
)

_PAROLA_NUMERO_ALT: Final[str] = (
    r"un|uno|una|due|tre|quattro|cinque|sei|sette|otto|nove|dieci|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|a|an"
)
_UNITA_ALT: Final[str] = (
    r"minuti|minuto|minutes|minute|ore|ora|hours|hour|"
    r"giorni|giorno|days|day|settimane|settimana|weeks|week|"
    r"mesi|mese|months|month"
)
_DIREZIONE_ALT: Final[str] = (
    r"dopo|later|after|seguente|seguenti|"
    r"prima|before|earlier|ago|precedente|precedenti|previous"
)

_OFFSET_TRA = re.compile(
    rf"\b(?:tra|in)\s+(?:(?P<num>\d+)|(?P<parola>{_PAROLA_NUMERO_ALT}))\s+"
    rf"(?P<unita>{_UNITA_ALT})"
    rf"(?:\s+(?P<direzione>{_DIREZIONE_ALT}))?\b",
    re.IGNORECASE,
)
_OFFSET_QUANTIFICATO = re.compile(
    rf"\b(?:(?P<num>\d+)|(?P<parola>{_PAROLA_NUMERO_ALT}))\s+"
    rf"(?P<unita>{_UNITA_ALT})\s+(?P<direzione>{_DIREZIONE_ALT})\b",
    re.IGNORECASE,
)
_OFFSET_SEGUENTE = re.compile(
    rf"\b(?:il|la|lo|l|the)\s+(?:(?P<dir_pre>next|following|previous)\s+)?"
    rf"(?P<unita>{_UNITA_ALT})"
    rf"(?:\s+(?P<dir_post>dopo|prima|seguente|seguenti|precedente|"
    rf"precedenti|after|before))?\b",
    re.IGNORECASE,
)
_GIORNO_DOPO = re.compile(
    r"\b(?:il\s+)?giorno dopo\b|\bthe day after\b|\bthe next day\b|"
    r"\bthe following day\b",
    re.IGNORECASE,
)
_SERA_DOPO = re.compile(
    r"\b(?:la\s+)?sera dopo\b|\bthe (?:evening|night) after\b",
    re.IGNORECASE,
)
_IERI = re.compile(r"\b(?:ieri|yesterday)\b", re.IGNORECASE)
_OGGI = re.compile(r"\b(?:oggi|today)\b", re.IGNORECASE)
_DOMANI = re.compile(r"\b(?:domani|tomorrow)\b", re.IGNORECASE)

ChiaveFusione = tuple[str, ...]


@dataclass(frozen=True)
class _OffsetRelativo:
    quantita: int
    unita: str


@dataclass(frozen=True)
class _EsitoIdentita:
    ancore: list[AncoraTemporaleProposta]
    n_in: int
    n_resolved_relative: int
    n_fuse: int
    n_out: int


def etichetta_normalizzata(valore: object) -> str:
    """Etichetta confrontabile: spazi collassati, maiuscole appiattite."""
    if not isinstance(valore, str):
        return ""
    return " ".join(valore.split()).casefold()


def identita_ancora(ancora: AncoraTemporaleProposta, documento: str) -> str:
    """Stable document-local id via ``ancora_temporale_id`` (never hashes etichetta)."""
    chiave = None
    if analizza(ancora.inizio) is None:
        chiave = _chiave_undated(ancora)
    return ancora_temporale_id(
        documento,
        ancora.natura,
        ancora.tipo,
        inizio=ancora.inizio,
        fine=ancora.fine,
        chiave=chiave,
    )


def normalizza_ancore(
    ancore: list[AncoraTemporaleProposta] | LivelloAncoreResult | object,
    *,
    documento: str,
    riferimento: str | None = None,
) -> list[AncoraTemporaleProposta]:
    """Resolve relatives, fuse by collocazione, uniquify labels. Pure and sync."""
    return _esito_identita(ancore, documento=documento, riferimento=riferimento).ancore


async def esegui_ancore_identita(
    ancore: list[AncoraTemporaleProposta] | LivelloAncoreResult | object,
    *,
    documento: str,
    riferimento: str | None = None,
    job_id: str | None = None,
) -> list[AncoraTemporaleProposta]:
    """Same as ``normalizza_ancore``; publishes a summary when ``job_id`` is set."""
    esito = _esito_identita(ancore, documento=documento, riferimento=riferimento)
    if job_id:
        await publish(
            job_id,
            STAGE,
            EVENTO,
            {
                "n_in": esito.n_in,
                "n_resolved_relative": esito.n_resolved_relative,
                "n_fuse": esito.n_fuse,
                "n_out": esito.n_out,
                "documento": documento,
            },
        )
    return esito.ancore


def _esito_identita(
    ancore: object,
    *,
    documento: str,
    riferimento: str | None,
) -> _EsitoIdentita:
    valide = _come_lista(ancore)
    n_in = len(valide)
    risolte, n_resolved = _risolvi_relative(valide, riferimento=riferimento)
    canoniche = [_normalizza_campi_iso(item) for item in risolte]
    fuse = _fondi_ancore(canoniche, documento=documento)
    n_fuse = max(0, len(canoniche) - len(fuse))
    univoche = _etichette_univoche(fuse)
    ordinati = _ordina(univoche)
    return _EsitoIdentita(
        ancore=ordinati,
        n_in=n_in,
        n_resolved_relative=n_resolved,
        n_fuse=n_fuse,
        n_out=len(ordinati),
    )


def _come_lista(ancore: object) -> list[AncoraTemporaleProposta]:
    if ancore is None:
        return []
    if isinstance(ancore, AncoraTemporaleProposta):
        return [ancore]
    if isinstance(ancore, LivelloAncoreResult):
        return list(ancore.ancore)
    if isinstance(ancore, Sequence) and not isinstance(ancore, (str, bytes)):
        return [item for item in ancore if isinstance(item, AncoraTemporaleProposta)]
    return []


def _testo(valore: object) -> str:
    return valore.strip() if isinstance(valore, str) else ""


def _chiave_undated(ancora: AncoraTemporaleProposta) -> str:
    for valore in (ancora.espressione, ancora.etichetta):
        chiave = etichetta_normalizzata(valore)
        if chiave:
            return chiave
    return ""


def _posizione(
    ancora: AncoraTemporaleProposta, indice: int = 0
) -> tuple[int, int, int]:
    pos = ancora.posizione_doc_min
    off = ancora.offset_inizio
    return (
        pos if isinstance(pos, int) else _POS_MANCANTE,
        off if isinstance(off, int) else _POS_MANCANTE,
        indice,
    )


def _ordinamento(
    ancora: AncoraTemporaleProposta, indice: int = 0
) -> tuple[int, int, int, int, str, int]:
    chiave = chiave_ordine(ancora.inizio, ancora.granularita)
    pos, off, _ = _posizione(ancora, indice)
    return (
        0 if chiave is not None else 1,
        chiave if chiave is not None else 0,
        pos,
        off,
        etichetta_normalizzata(ancora.etichetta),
        indice,
    )


def _ordina(ancore: list[AncoraTemporaleProposta]) -> list[AncoraTemporaleProposta]:
    return [
        ancora
        for _, ancora in sorted(enumerate(ancore), key=lambda p: _ordinamento(p[1], p[0]))
    ]


def _offset_relativo(ancora: AncoraTemporaleProposta) -> _OffsetRelativo | None:
    if ancora.natura != "esplicita":
        return None
    testo = _testo(ancora.espressione) or _testo(ancora.etichetta)
    if not testo:
        return None
    return _parse_offset(testo)


def _blob_relativo(testo: str) -> str:
    return " ".join(testo.replace("'", " ").replace("\u2019", " ").split())


def _offset_da_match(
    match: re.Match[str],
    *,
    direzione: str | None = None,
    quantita: int | None = None,
) -> _OffsetRelativo | None:
    unita = _UNITA_CANONICA.get((match.group("unita") or "").casefold())
    if unita is None:
        return None
    if quantita is None:
        if match.group("num"):
            try:
                quantita = int(match.group("num"))
            except ValueError:
                return None
        else:
            quantita = _PAROLE_NUMERO.get((match.group("parola") or "").casefold())
        if quantita is None:
            return None
    token = direzione if direzione is not None else match.groupdict().get("direzione")
    if (token or "").casefold() in _DIREZIONE_PASSATO:
        quantita = -quantita
    return _OffsetRelativo(quantita, unita)


def _parse_offset(testo: str) -> _OffsetRelativo | None:
    blob = _blob_relativo(testo)
    match = _OFFSET_TRA.search(blob)
    if match is not None:
        parsed = _offset_da_match(match)
        if parsed is not None:
            return parsed
    match = _OFFSET_QUANTIFICATO.search(blob)
    if match is not None:
        parsed = _offset_da_match(match)
        if parsed is not None:
            return parsed
    match = _OFFSET_SEGUENTE.search(blob)
    if match is not None:
        direzione = match.group("dir_pre") or match.group("dir_post")
        if direzione:
            parsed = _offset_da_match(match, direzione=direzione, quantita=1)
            if parsed is not None:
                return parsed
    if _GIORNO_DOPO.search(blob) is not None or _SERA_DOPO.search(blob) is not None:
        return _OffsetRelativo(1, "giorno")
    if _IERI.search(blob) is not None:
        return _OffsetRelativo(-1, "giorno")
    if _DOMANI.search(blob) is not None:
        return _OffsetRelativo(1, "giorno")
    if _OGGI.search(blob) is not None:
        return _OffsetRelativo(0, "giorno")
    return None


def _tempo_riferimento_usabile(valore: object) -> TempoISO | None:
    tempo = analizza(valore)
    if tempo is None or tempo.precisione not in _PRECISIONE_ALMENO_GIORNO:
        return None
    return tempo


def _iso_a_precisione(spostato: datetime, precisione: str) -> str:
    if precisione == "anno":
        return f"{spostato.year:04d}"
    if precisione == "mese":
        return f"{spostato.year:04d}-{spostato.month:02d}"
    testo = f"{spostato.year:04d}-{spostato.month:02d}-{spostato.day:02d}"
    if precisione in {"giorno", "settimana"}:
        return testo
    testo += f"T{spostato.hour:02d}"
    if precisione == "ora":
        return testo
    testo += f":{spostato.minute:02d}"
    if precisione == "minuto":
        return testo
    return f"{testo}:{spostato.second:02d}"


def _sposta_mesi(base: datetime, mesi: int) -> datetime | None:
    totale = base.year * 12 + (base.month - 1) + mesi
    anno, indice = divmod(totale, 12)
    mese = indice + 1
    if not 1 <= anno <= 9999:
        return None
    giorno = min(base.day, calendar.monthrange(anno, mese)[1])
    try:
        return datetime(anno, mese, giorno, base.hour, base.minute, base.second)
    except ValueError:
        return None


def _applica_offset(tempo: TempoISO, offset: _OffsetRelativo) -> TempoISO | None:
    try:
        base = datetime(
            tempo.anno,
            tempo.mese,
            tempo.giorno,
            tempo.ora,
            tempo.minuto,
            tempo.secondo,
        )
        if offset.unita == "mese":
            spostato = _sposta_mesi(base, offset.quantita)
        elif offset.unita == "minuto":
            spostato = base + timedelta(minutes=offset.quantita)
        elif offset.unita == "ora":
            spostato = base + timedelta(hours=offset.quantita)
        elif offset.unita == "giorno":
            spostato = base + timedelta(days=offset.quantita)
        elif offset.unita == "settimana":
            spostato = base + timedelta(weeks=offset.quantita)
        else:
            return None
    except (ValueError, OverflowError):
        return None
    if spostato is None or not 1 <= spostato.year <= 9999:
        return None
    unita_prec = _UNITA_PRECISIONE.get(offset.unita)
    if unita_prec is None:
        return None
    effettiva = _piu_fine(tempo.precisione, unita_prec) or unita_prec
    return analizza(_iso_a_precisione(spostato, effettiva))


def _riferimento_precedente(
    pos: tuple[int, int, int],
    datate: list[tuple[tuple[int, int, int], TempoISO]],
) -> TempoISO | None:
    precedenti = [(p, tempo) for p, tempo in datate if p < pos]
    if not precedenti:
        return None
    return max(precedenti, key=lambda item: item[0])[1]


def _risolvi_relative(
    ancore: list[AncoraTemporaleProposta],
    *,
    riferimento: str | None,
) -> tuple[list[AncoraTemporaleProposta], int]:
    """Place relative expressions on a dated reference.

    Without a dated reference ancora the relative expression stays without
    ``inizio`` and lives only as an ordered ancora on the chain, never as a
    dated ancora. A date is never invented.
    """
    fallback = _tempo_riferimento_usabile(riferimento)
    indicizzate = list(enumerate(ancore))
    indicizzate.sort(key=lambda p: _posizione(p[1], p[0]))
    n_resolved = 0
    datate: list[tuple[tuple[int, int, int], TempoISO]] = []
    per_indice: dict[int, AncoraTemporaleProposta] = {}
    for indice, ancora in indicizzate:
        pos = _posizione(ancora, indice)
        copia = ancora
        offset = _offset_relativo(ancora)
        if offset is not None and analizza(ancora.inizio) is None:
            ref = _riferimento_precedente(pos, datate) or fallback
            risolto = _applica_offset(ref, offset) if ref is not None else None
            if risolto is not None:
                copia = ancora.model_copy(
                    update={
                        "inizio": risolto.canonico,
                        "stimato": True,
                        "granularita": risolto.precisione,
                    }
                )
                n_resolved += 1
        per_indice[indice] = copia
        if not copia.stimato:
            tempo = _tempo_riferimento_usabile(copia.inizio)
            if tempo is not None:
                datate.append((pos, tempo))
    return [per_indice[i] for i in range(len(ancore))], n_resolved


def _normalizza_campi_iso(ancora: AncoraTemporaleProposta) -> AncoraTemporaleProposta:
    iso = normalizza(ancora.inizio, ancora.granularita)
    if iso is None:
        return ancora
    canonico, effettiva = iso
    update: dict[str, object] = {"inizio": canonico}
    if ancora.granularita is None:
        update["granularita"] = effettiva
    fine = analizza(ancora.fine)
    if fine is not None:
        update["fine"] = fine.canonico
    return ancora.model_copy(update=update)


def _chiave_fusione(ancora: AncoraTemporaleProposta, documento: str) -> ChiaveFusione | None:
    iso = normalizza(ancora.inizio, ancora.granularita)
    if iso is not None:
        canonico, effettiva = iso
        return (CHIAVE_ISO, documento, canonico, effettiva)
    testo = _chiave_undated(ancora)
    if not testo:
        return None
    return (CHIAVE_ETICHETTA, documento, ancora.natura, ancora.tipo, testo)


def _rango_etichetta(etichetta: str) -> tuple[int, int, str]:
    lunghezza = len(etichetta)
    if lunghezza <= ETICHETTA_MAX_CHAR:
        return (0, -lunghezza, etichetta.casefold())
    return (1, lunghezza, etichetta.casefold())


def _etichette_fuse(prima: object, seconda: object) -> str:
    candidate = [testo for testo in (_testo(prima), _testo(seconda)) if testo]
    if not candidate:
        return ""
    return min(candidate, key=_rango_etichetta)


def _min_int(prima: int | None, seconda: int | None) -> int | None:
    if prima is None:
        return seconda
    if seconda is None:
        return prima
    return min(prima, seconda)


def _piu_fine(prima: object, seconda: object) -> str | None:
    rango_prima = rango_granularita(prima)
    rango_seconda = rango_granularita(seconda)
    if rango_prima is None:
        return seconda if isinstance(seconda, str) else None
    if rango_seconda is None:
        return prima if isinstance(prima, str) else None
    return prima if rango_prima <= rango_seconda else seconda


def _preferisci_tipo(prima: TipoAncora, seconda: TipoAncora) -> TipoAncora:
    if _TIPO_RANGO.get(prima, 99) <= _TIPO_RANGO.get(seconda, 99):
        return prima
    return seconda


def _preferisci_natura(prima: NaturaAncora, seconda: NaturaAncora) -> NaturaAncora:
    if _NATURA_RANGO.get(prima, 99) <= _NATURA_RANGO.get(seconda, 99):
        return prima
    return seconda


def _unisci_eventi(
    prima: AncoraTemporaleProposta, seconda: AncoraTemporaleProposta
) -> list[str]:
    fuori: list[str] = []
    visti: set[str] = set()
    for eid in list(prima.eventi or []) + list(seconda.eventi or []):
        if isinstance(eid, str) and eid and eid not in visti:
            visti.add(eid)
            fuori.append(eid)
    return fuori


def _scegli_inizio(
    prima: AncoraTemporaleProposta, seconda: AncoraTemporaleProposta
) -> tuple[str | None, bool]:
    if prima.inizio and not seconda.inizio:
        return prima.inizio, prima.stimato
    if seconda.inizio and not prima.inizio:
        return seconda.inizio, seconda.stimato
    if not prima.inizio and not seconda.inizio:
        return None, prima.stimato and seconda.stimato
    if prima.stimato and not seconda.stimato:
        return seconda.inizio, False
    if seconda.stimato and not prima.stimato:
        return prima.inizio, False
    return prima.inizio, prima.stimato and seconda.stimato


def _scegli_span(
    prima: AncoraTemporaleProposta, seconda: AncoraTemporaleProposta
) -> tuple[int | None, int | None, str | None]:
    if _posizione(prima) <= _posizione(seconda):
        scelto = prima if prima.offset_inizio is not None else seconda
    else:
        scelto = seconda if seconda.offset_inizio is not None else prima
    espressione = prima.espressione or seconda.espressione
    if scelto.espressione:
        espressione = scelto.espressione
    return scelto.offset_inizio, scelto.offset_fine, espressione


def _fondi_coppia(
    prima: AncoraTemporaleProposta, seconda: AncoraTemporaleProposta
) -> AncoraTemporaleProposta:
    etichetta = _etichette_fuse(prima.etichetta, seconda.etichetta) or prima.etichetta
    inizio, stimato = _scegli_inizio(prima, seconda)
    off_i, off_f, espressione = _scegli_span(prima, seconda)
    descrizione = prima.descrizione or seconda.descrizione
    return prima.model_copy(
        update={
            "etichetta": etichetta,
            "natura": _preferisci_natura(prima.natura, seconda.natura),
            "tipo": _preferisci_tipo(prima.tipo, seconda.tipo),
            "eventi": _unisci_eventi(prima, seconda),
            "descrizione": descrizione,
            "granularita": _piu_fine(prima.granularita, seconda.granularita)
            or prima.granularita
            or seconda.granularita,
            "inizio": inizio,
            "fine": prima.fine or seconda.fine,
            "espressione": espressione,
            "offset_inizio": off_i,
            "offset_fine": off_f,
            "stimato": stimato,
            "confidenza": min(prima.confidenza, seconda.confidenza),
            "posizione_doc_min": _min_int(
                prima.posizione_doc_min, seconda.posizione_doc_min
            ),
            "padre": prima.padre or seconda.padre,
        }
    )


def _fondi_gruppo(
    membri: list[AncoraTemporaleProposta], chiave: ChiaveFusione
) -> AncoraTemporaleProposta:
    ordinati = [
        ancora
        for _, ancora in sorted(
            enumerate(membri), key=lambda p: _posizione(p[1], p[0])
        )
    ]
    acc = ordinati[0]
    for altro in ordinati[1:]:
        acc = _fondi_coppia(acc, altro)
    if chiave and chiave[0] == CHIAVE_ISO:
        canonico = chiave[2]
        effettiva = chiave[3]
        dichiarate = [m.granularita for m in ordinati if m.granularita]
        granularita = effettiva
        if dichiarate:
            finer: object = dichiarate[0]
            for extra in dichiarate[1:]:
                finer = _piu_fine(finer, extra) or finer
            if isinstance(finer, str):
                granularita = finer
        acc = acc.model_copy(update={"inizio": canonico, "granularita": granularita})
    return acc


def _fondi_ancore(
    ancore: list[AncoraTemporaleProposta], *, documento: str
) -> list[AncoraTemporaleProposta]:
    gruppi: dict[ChiaveFusione, list[AncoraTemporaleProposta]] = {}
    ordine: list[ChiaveFusione] = []
    sciolte: list[AncoraTemporaleProposta] = []
    for ancora in ancore:
        chiave = _chiave_fusione(ancora, documento)
        if chiave is None:
            sciolte.append(ancora)
            continue
        if chiave not in gruppi:
            gruppi[chiave] = []
            ordine.append(chiave)
        gruppi[chiave].append(ancora)
    fuse = [_fondi_gruppo(gruppi[chiave], chiave) for chiave in ordine]
    fuse.extend(sciolte)
    return fuse


def _coda_disambiguazione(ancora: AncoraTemporaleProposta, posizione: int) -> str:
    tempo = analizza(ancora.inizio)
    if tempo is not None:
        return tempo.canonico
    return str(posizione)


def _etichetta_con_suffisso(base: str, coda: str) -> str:
    extra = f" ({coda.strip()})"
    if len(extra) >= ETICHETTA_MAX_CHAR:
        return extra[:ETICHETTA_MAX_CHAR]
    room = ETICHETTA_MAX_CHAR - len(extra)
    tronco = base[:room].rstrip()
    if not tronco:
        return extra[:ETICHETTA_MAX_CHAR]
    return f"{tronco}{extra}"


def _etichetta_base(ancora: AncoraTemporaleProposta) -> str:
    base = _testo(ancora.etichetta) or _testo(ancora.espressione)
    if base:
        return base
    tempo = analizza(ancora.inizio)
    if tempo is not None:
        return tempo.canonico
    return "ancora"


def _etichette_univoche(
    ancore: list[AncoraTemporaleProposta],
) -> list[AncoraTemporaleProposta]:
    if not ancore:
        return []
    ordine = sorted(range(len(ancore)), key=lambda i: _ordinamento(ancore[i], i))
    prese: set[str] = set()
    nuovi: dict[int, str] = {}
    vecchi: dict[int, str] = {}
    for posizione, indice in enumerate(ordine, start=1):
        ancora = ancore[indice]
        base = _etichetta_base(ancora)
        vecchi[indice] = ancora.etichetta
        candidato = base[:ETICHETTA_MAX_CHAR]
        if etichetta_normalizzata(candidato) in prese:
            candidato = _etichetta_con_suffisso(base, _coda_disambiguazione(ancora, posizione))
        contatore = 2
        while etichetta_normalizzata(candidato) in prese:
            candidato = _etichetta_con_suffisso(base, str(contatore))
            contatore += 1
        prese.add(etichetta_normalizzata(candidato))
        nuovi[indice] = candidato

    old_to_idx: dict[str, list[int]] = {}
    for indice, vecchia in vecchi.items():
        chiave = etichetta_normalizzata(vecchia)
        if chiave:
            old_to_idx.setdefault(chiave, []).append(indice)
    new_by_norm = {etichetta_normalizzata(nome): nome for nome in nuovi.values()}
    new_norms = set(new_by_norm)

    fuori: list[AncoraTemporaleProposta] = []
    for indice, ancora in enumerate(ancore):
        padre = ancora.padre
        nuovo_padre = padre
        if padre:
            pk = etichetta_normalizzata(padre)
            if pk in new_by_norm:
                nuovo_padre = new_by_norm[pk]
            elif pk in old_to_idx and len(old_to_idx[pk]) == 1:
                nuovo_padre = nuovi[old_to_idx[pk][0]]
            if etichetta_normalizzata(nuovo_padre) not in new_norms:
                nuovo_padre = None
            if etichetta_normalizzata(nuovo_padre) == etichetta_normalizzata(
                nuovi[indice]
            ):
                nuovo_padre = None
        fuori.append(
            ancora.model_copy(update={"etichetta": nuovi[indice], "padre": nuovo_padre})
        )
    return fuori


__all__ = [
    "ETICHETTA_MAX_CHAR",
    "EVENTO",
    "STAGE",
    "etichetta_normalizzata",
    "esegui_ancore_identita",
    "identita_ancora",
    "normalizza_ancore",
]
