"""MT2 — extract explicit temporal anchors from each zone's text.

One structured LLM call per zona, on ``zona.testo`` only — never the full
document. A deterministic regex prepass plus ``zona.ancore_temporali`` seeds
the proposals so obvious years, ISO dates and clock times do not depend on
the model. LLM failure is logged and published; prepass+seed still return.

Isolation D6: ``app.pipeline.event_graph.*`` and ``app.models.event_graph``
only. LLM exclusively via ``infra.llm.call_structured``.
"""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.models.event_graph import (
    AncoraTemporaleProposta,
    LivelloAncoreResult,
    TipoAncora,
)
from app.pipeline.event_graph.infra.bus import publish
from app.pipeline.event_graph.infra.llm import call_structured as _call_structured
from app.pipeline.event_graph.tempo_iso import analizza
from app.pipeline.event_graph.zona_segmentation import Zona

logger = logging.getLogger(__name__)

# Defensive cap so a whole-document "zone" cannot overflow context the way
# v2 did on christmas-carol.txt (the full text in every window).
MAX_CHAR_TESTO_ZONA = 12_000

STAGE = "collocazione_temporale"
EVENTO_ZONA = "ancore_estrazione_zona"
EVENTO_DOCUMENTO = "ancore_estrazione"

_ISO_IN_TESTO = re.compile(
    r"(?<![\d-])"
    r"(\d{4}(?:-\d{2}(?:-\d{2}(?:[Tt ]\d{2}(?::\d{2}(?::\d{2})?)?)?)?)?)"
    r"(?![\d-])"
)
_DATA_NUMERICA = re.compile(r"(?<!\d)(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})(?!\d)")
_ORARIO_HM = re.compile(r"(?<!\d)(\d{1,2}:\d{2}(?::\d{2})?)(?!\d)")
_ALLE_ORA = re.compile(
    r"\b(?:alle|all['’]|ore)\s+\d{1,2}(?::\d{2}(?::\d{2})?)?\b",
    re.IGNORECASE,
)
_RELATIVE = frozenset(
    {
        "ieri",
        "oggi",
        "domani",
        "yesterday",
        "today",
        "tomorrow",
    }
)

SYSTEM_ANCORE_ESTRAZIONE = """\
You extract temporal anchors from ONE lexical zone of Italian or English text.

Return structured output only. Temperature is 0. Never call tools.

For every temporal expression in THIS zone text (dates, years, clock times,
deadlines, epochs, relative words like ieri/domani, symbolic moments like
Natale), emit one ancora:

- espressione: the literal span as written
- offset_inizio / offset_fine: 0-based character offsets into the zone text
  provided below (NOT into the full document). offset_fine is exclusive.
- inizio: ISO 8601 at variable precision ONLY if that ISO string is written
  in the span or zone text (1843, 1843-12-24, 1843-12-24T18:30). Do not
  invent a year, month, day, or clock. Natale does not become 1843.
- granularita: secondo→secolo, only when justified by the written form
- tipo: data | ora | scadenza | epoca | relativa | simbolica
- etichetta: short, at most 40 characters
- natura: esplicita
- stimato: false
- eventi: always empty (do not assign events)

Do not create implicit intervallo or aperta anchors.
Do not resolve ieri/domani to a calendar date.
Do not copy expressions that are not in this zone.
"""


class AncoreZonaLlm(BaseModel):
    """Structured LLM payload for one zone — ancore only, no event signals."""

    ancore: list[AncoraTemporaleProposta] = Field(default_factory=list)


@dataclass
class EstrazioneAncoreResult:
    """LivelloAncoreResult plus extraction diagnostics (never silent)."""

    livello: LivelloAncoreResult = field(default_factory=LivelloAncoreResult)
    n_zone: int = 0
    n_ancore: int = 0
    n_llm_failures: int = 0
    n_zone_vuote: int = 0

    @property
    def ancore(self) -> list[AncoraTemporaleProposta]:
        return self.livello.ancore

    @property
    def segnali(self) -> list:
        return self.livello.segnali


def _tronca(testo: str, massimo: int) -> str:
    if len(testo) <= massimo:
        return testo
    return testo[:massimo].rstrip() + "…"


def _etichetta(espressione: str) -> str:
    testo = " ".join((espressione or "").split())
    return testo[:40] if testo else "ancora"


def _inizio_se_iso(span: str | None) -> tuple[str | None, str | None]:
    if not span:
        return None, None
    tempo = analizza(span.strip())
    if tempo is None:
        return None, None
    return tempo.canonico, tempo.precisione


def _inizio_giustificato(espressione: str | None, candidato: str | None) -> str | None:
    """Keep ISO only when the expression itself is ISO, or the ISO is written in it."""
    da_span, _gran = _inizio_se_iso(espressione)
    if da_span is not None:
        return da_span
    if not candidato or not espressione:
        return None
    tempo = analizza(str(candidato).strip())
    if tempo is None:
        return None
    hay = espressione
    if tempo.canonico in hay or str(candidato) in hay:
        return tempo.canonico
    return None


def _tipo_da_span(span: str) -> TipoAncora:
    token = (span or "").strip().casefold()
    if token in _RELATIVE:
        return "relativa"
    if _ALLE_ORA.fullmatch(span.strip() if span else "") or ":" in (span or ""):
        return "ora"
    data_numerica = _DATA_NUMERICA.fullmatch(span.strip() if span else "")
    if _inizio_se_iso(span)[0] is not None or data_numerica:
        return "data"
    return "simbolica"


def _orario_valido(span: str) -> bool:
    trovato = re.search(r"(\d{1,2})(?::(\d{2}))?(?::(\d{2}))?\s*$", span.strip())
    if trovato is None:
        return False
    ora = int(trovato.group(1))
    minuto = int(trovato.group(2) or 0)
    secondo = int(trovato.group(3) or 0)
    return 0 <= ora <= 23 and 0 <= minuto <= 59 and 0 <= secondo <= 59


def _proposta(
    *,
    espressione: str,
    offset_inizio: int | None,
    offset_fine: int | None,
    tipo: TipoAncora,
    inizio: str | None = None,
    granularita: str | None = None,
) -> AncoraTemporaleProposta:
    inizio_ok = _inizio_giustificato(espressione, inizio)
    gran = None
    if inizio_ok:
        tempo = analizza(inizio_ok)
        gran = granularita or (tempo.precisione if tempo is not None else None)
    return AncoraTemporaleProposta(
        etichetta=_etichetta(espressione),
        natura="esplicita",
        tipo=tipo,
        eventi=[],
        granularita=gran,
        inizio=inizio_ok,
        espressione=espressione,
        offset_inizio=offset_inizio,
        offset_fine=offset_fine,
        stimato=False,
        posizione_doc_min=offset_inizio,
    )


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    tipo: TipoAncora


def _raccogli_hit(testo: str) -> list[_Hit]:
    grezzi: list[_Hit] = []
    for match in _ISO_IN_TESTO.finditer(testo):
        span = match.group(1)
        ha_orario = bool(re.search(r"[Tt ]\d{2}", span))
        tempo = analizza(span)
        if "-" not in span and tempo is None:
            continue
        tipo: TipoAncora = "ora" if ha_orario else "data"
        grezzi.append(_Hit(match.start(1), match.end(1), tipo))
    for match in _DATA_NUMERICA.finditer(testo):
        grezzi.append(_Hit(match.start(1), match.end(1), "data"))
    for match in _ORARIO_HM.finditer(testo):
        span = match.group(1)
        if not _orario_valido(span):
            continue
        grezzi.append(_Hit(match.start(1), match.end(1), "ora"))
    for match in _ALLE_ORA.finditer(testo):
        span = match.group(0)
        if not _orario_valido(span):
            continue
        grezzi.append(_Hit(match.start(), match.end(), "ora"))

    grezzi.sort(key=lambda hit: (hit.start, -(hit.end - hit.start)))
    tenuti: list[_Hit] = []
    for hit in grezzi:
        contenuto = False
        for gia in tenuti:
            if gia.start <= hit.start and hit.end <= gia.end and (gia.start, gia.end) != (
                hit.start,
                hit.end,
            ):
                contenuto = True
                break
            if gia.start == hit.start and gia.end == hit.end:
                contenuto = True
                break
        if not contenuto:
            tenuti.append(hit)
    return tenuti


def prepass_regex(
    testo: str,
    *,
    offset_documento: int = 0,
) -> list[AncoraTemporaleProposta]:
    """Deterministic year / ISO / clock-time hits. No LLM."""
    if not testo:
        return []
    ancore: list[AncoraTemporaleProposta] = []
    for hit in _raccogli_hit(testo):
        span = testo[hit.start : hit.end]
        inizio, gran = _inizio_se_iso(span)
        doc_i = offset_documento + hit.start
        doc_f = offset_documento + hit.end
        ancore.append(
            _proposta(
                espressione=span,
                offset_inizio=doc_i,
                offset_fine=doc_f,
                tipo=hit.tipo,
                inizio=inizio,
                granularita=gran,
            )
        )
    return ancore


def _prima_occorrenza(testo: str, ago: str) -> int:
    if not testo or not ago:
        return -1
    idx = testo.find(ago)
    if idx >= 0:
        return idx
    return testo.casefold().find(ago.casefold())


def proposte_da_semi(
    testo: str,
    semi: Sequence[str] | None,
    *,
    offset_documento: int = 0,
) -> list[AncoraTemporaleProposta]:
    """Turn ``zona.ancore_temporali`` into proposals; offsets if found in text."""
    ancore: list[AncoraTemporaleProposta] = []
    visti: set[str] = set()
    for grezzo in semi or []:
        if not isinstance(grezzo, str):
            continue
        espressione = grezzo.strip()
        if not espressione:
            continue
        chiave = espressione.casefold()
        if chiave in visti:
            continue
        visti.add(chiave)
        idx = _prima_occorrenza(testo or "", espressione)
        if idx >= 0:
            inizio, gran = _inizio_se_iso(testo[idx : idx + len(espressione)])
            if inizio is None:
                inizio, gran = _inizio_se_iso(espressione)
            ancore.append(
                _proposta(
                    espressione=testo[idx : idx + len(espressione)]
                    if testo[idx : idx + len(espressione)]
                    else espressione,
                    offset_inizio=offset_documento + idx,
                    offset_fine=offset_documento + idx + len(espressione),
                    tipo=_tipo_da_span(espressione),
                    inizio=inizio,
                    granularita=gran,
                )
            )
        else:
            inizio, gran = _inizio_se_iso(espressione)
            ancore.append(
                _proposta(
                    espressione=espressione,
                    offset_inizio=None,
                    offset_fine=None,
                    tipo=_tipo_da_span(espressione),
                    inizio=inizio,
                    granularita=gran,
                )
            )
    return ancore


def _sovrapposte(a: AncoraTemporaleProposta, b: AncoraTemporaleProposta) -> bool:
    if a.offset_inizio is None or a.offset_fine is None:
        return False
    if b.offset_inizio is None or b.offset_fine is None:
        return False
    return a.offset_inizio < b.offset_fine and b.offset_inizio < a.offset_fine


def _stessa_espressione(a: AncoraTemporaleProposta, b: AncoraTemporaleProposta) -> bool:
    ea = (a.espressione or "").strip().casefold()
    eb = (b.espressione or "").strip().casefold()
    return bool(ea) and ea == eb


def _unisci_due(
    base: AncoraTemporaleProposta, extra: AncoraTemporaleProposta
) -> AncoraTemporaleProposta:
    espressione = base.espressione or extra.espressione
    extra_expr = extra.espressione or ""
    base_expr = base.espressione or ""
    if extra_expr and len(extra_expr) > len(base_expr):
        espressione = extra_expr

    off_i, off_f = base.offset_inizio, base.offset_fine
    if off_i is None or off_f is None:
        off_i, off_f = extra.offset_inizio, extra.offset_fine
    elif extra.offset_inizio is not None and extra.offset_fine is not None:
        if extra.offset_fine - extra.offset_inizio > off_f - off_i:
            off_i, off_f = extra.offset_inizio, extra.offset_fine
            if extra.espressione:
                espressione = extra.espressione

    inizio = _inizio_giustificato(espressione, base.inizio or extra.inizio)
    if extra.tipo in ("data", "ora") and base.tipo not in ("data", "ora"):
        tipo: TipoAncora = extra.tipo
    elif base.tipo in ("data", "ora"):
        tipo = base.tipo
        span = espressione or ""
        if extra.tipo == "ora" and (":" in span or _ALLE_ORA.search(span)):
            tipo = "ora"
    else:
        tipo = extra.tipo if extra.tipo != "simbolica" else base.tipo

    etichetta = extra.etichetta or base.etichetta or _etichetta(espressione or "")
    granularita = base.granularita or extra.granularita
    if inizio:
        tempo = analizza(inizio)
        if tempo is not None and granularita is None:
            granularita = tempo.precisione

    fine = _inizio_giustificato(espressione, base.fine or extra.fine)
    return AncoraTemporaleProposta(
        etichetta=etichetta,
        natura="esplicita",
        tipo=tipo,
        eventi=[],
        descrizione=base.descrizione or extra.descrizione,
        granularita=granularita,
        inizio=inizio,
        fine=fine,
        espressione=espressione,
        offset_inizio=off_i,
        offset_fine=off_f,
        stimato=False,
        confidenza=max(base.confidenza, extra.confidenza),
        posizione_doc_min=off_i,
        padre=base.padre or extra.padre,
    )


def unisci_proposte(
    *gruppi: Sequence[AncoraTemporaleProposta],
) -> list[AncoraTemporaleProposta]:
    """Merge by overlapping document span or equal espressione. Earlier groups win identity."""
    tenute: list[AncoraTemporaleProposta] = []
    for gruppo in gruppi:
        for proposta in gruppo:
            indice = next(
                (
                    i
                    for i, gia in enumerate(tenute)
                    if _stessa_espressione(gia, proposta) or _sovrapposte(gia, proposta)
                ),
                None,
            )
            if indice is None:
                tenute.append(proposta)
            else:
                tenute[indice] = _unisci_due(tenute[indice], proposta)
    return tenute


def _offset_zona_locale(
    zona: Zona, ancora: AncoraTemporaleProposta
) -> tuple[int | None, int | None]:
    if ancora.offset_inizio is None or ancora.offset_fine is None:
        return None, None
    return (
        ancora.offset_inizio - zona.offset_inizio,
        ancora.offset_fine - zona.offset_inizio,
    )


def _riga_prepass(zona: Zona, ancora: AncoraTemporaleProposta) -> str:
    loc_i, loc_f = _offset_zona_locale(zona, ancora)
    span = ancora.espressione or ""
    parti = [span]
    if loc_i is not None and loc_f is not None:
        parti.append(f"{loc_i}-{loc_f}")
    parti.append(ancora.tipo)
    if ancora.inizio:
        parti.append(f"inizio={ancora.inizio}")
    return " | ".join(parti)


def user_ancore_zona(
    zona: Zona,
    *,
    regex_hits: Sequence[AncoraTemporaleProposta] | None = None,
) -> str:
    """Prompt for one zone — only this zone's text, truncated if huge."""
    testo = zona.testo or ""
    testo_prompt = _tronca(testo, MAX_CHAR_TESTO_ZONA)
    troncato = len(testo) > MAX_CHAR_TESTO_ZONA
    semi = [
        item.strip()
        for item in (zona.ancore_temporali or [])
        if isinstance(item, str) and item.strip()
    ]
    righe_semi = "\n".join(f"- {item}" for item in semi) or "- (nessuna)"
    righe_regex = (
        "\n".join(f"- {_riga_prepass(zona, hit)}" for hit in (regex_hits or [])) or "- (nessuno)"
    )
    testa_testo = (
        f"TESTO DELLA ZONA (troncato, primi {MAX_CHAR_TESTO_ZONA} caratteri):"
        if troncato
        else "TESTO DELLA ZONA:"
    )
    return (
        "Estrai le ancore temporali SOLO da questo testo di zona.\n"
        "Non usare altro testo. Non inventare date.\n"
        "offset_inizio/offset_fine sono indici 0-based nel TESTO DELLA ZONA.\n\n"
        f"zona_id: {zona.id}\n"
        f"ordinale: {zona.ordinale}\n"
        f"documento: {zona.documento}\n\n"
        f"Semi da zona.ancore_temporali:\n{righe_semi}\n\n"
        f"Prepass regex (offset nel testo di questa zona):\n{righe_regex}\n\n"
        f"{testa_testo}\n{testo_prompt}\n"
    )


def _offset_documento(
    zona: Zona,
    offset_inizio: int | None,
    offset_fine: int | None,
    espressione: str | None,
) -> tuple[int | None, int | None]:
    testo = zona.testo or ""
    n = len(testo)
    base = zona.offset_inizio
    if (
        isinstance(offset_inizio, int)
        and isinstance(offset_fine, int)
        and offset_inizio < offset_fine
    ):
        if 0 <= offset_inizio and offset_fine <= n:
            return base + offset_inizio, base + offset_fine
        fine_doc = base + n
        if base <= offset_inizio and offset_fine <= fine_doc:
            return offset_inizio, offset_fine
    if espressione:
        idx = _prima_occorrenza(testo, espressione)
        if idx >= 0:
            return base + idx, base + idx + len(espressione)
    return None, None


def _sanifica_llm(
    grezza: AncoraTemporaleProposta, zona: Zona
) -> AncoraTemporaleProposta | None:
    espressione = (grezza.espressione or "").strip()
    if not espressione:
        return None
    off_i, off_f = _offset_documento(
        zona, grezza.offset_inizio, grezza.offset_fine, espressione
    )
    if off_i is not None and off_f is not None and off_i < zona.offset_inizio:
        off_i, off_f = None, None
    tipo: TipoAncora = grezza.tipo if grezza.tipo in (
        "data",
        "ora",
        "scadenza",
        "epoca",
        "relativa",
        "simbolica",
    ) else _tipo_da_span(espressione)
    inizio = _inizio_giustificato(espressione, grezza.inizio)
    return AncoraTemporaleProposta(
        etichetta=grezza.etichetta or _etichetta(espressione),
        natura="esplicita",
        tipo=tipo,
        eventi=[],
        descrizione=grezza.descrizione,
        granularita=grezza.granularita if inizio else None,
        inizio=inizio,
        fine=_inizio_giustificato(espressione, grezza.fine),
        espressione=espressione,
        offset_inizio=off_i,
        offset_fine=off_f,
        stimato=False,
        confidenza=grezza.confidenza,
        posizione_doc_min=off_i,
        padre=grezza.padre,
    )


def _ancore_da_parsed(parsed: Any) -> list[AncoraTemporaleProposta]:
    if isinstance(parsed, AncoreZonaLlm):
        return list(parsed.ancore)
    if isinstance(parsed, LivelloAncoreResult):
        return list(parsed.ancore)
    try:
        return list(AncoreZonaLlm.model_validate(parsed).ancore)
    except (ValidationError, TypeError, ValueError):
        return []


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _chiama_zona(
    zona: Zona,
    user_prompt: str,
    *,
    job_id: str | None,
    llm: Any,
) -> tuple[list[AncoraTemporaleProposta], bool]:
    try:
        parsed = await _maybe_await(
            llm(
                SYSTEM_ANCORE_ESTRAZIONE,
                user_prompt,
                AncoreZonaLlm,
                temperature=0,
                job_id=job_id,
            )
        )
    except Exception:
        logger.exception(
            "ancore_estrazione zona=%s llm failed; keeping prepass+seed",
            getattr(zona, "id", None),
        )
        return [], True
    sane: list[AncoraTemporaleProposta] = []
    for grezza in _ancore_da_parsed(parsed):
        pulita = _sanifica_llm(grezza, zona)
        if pulita is not None:
            sane.append(pulita)
    return sane, False


async def _pubblica(
    job_id: str | None,
    event: str,
    payload: dict[str, Any],
) -> None:
    if not job_id:
        return
    await publish(job_id, STAGE, event, payload)


async def estrai_ancore(
    zone: list[Zona] | None,
    *,
    job_id: str | None = None,
    call_structured: Any = None,
) -> EstrazioneAncoreResult:
    """One LLM call per zona on that zona's text. Never returns None."""
    llm = call_structured if call_structured is not None else _call_structured
    try:
        items = list(zone or [])
    except TypeError:
        items = []

    tutte: list[AncoraTemporaleProposta] = []
    n_llm_failures = 0
    n_zone_vuote = 0

    for zona in items:
        testo = zona.testo or ""
        regex_hits = prepass_regex(testo, offset_documento=zona.offset_inizio)
        seed_hits = proposte_da_semi(
            testo, zona.ancore_temporali, offset_documento=zona.offset_inizio
        )
        llm_hits: list[AncoraTemporaleProposta] = []
        failed = False
        if testo.strip():
            user_prompt = user_ancore_zona(zona, regex_hits=regex_hits)
            llm_hits, failed = await _chiama_zona(
                zona, user_prompt, job_id=job_id, llm=llm
            )
            if failed:
                n_llm_failures += 1
        merged = unisci_proposte(regex_hits, seed_hits, llm_hits)
        if not merged:
            n_zone_vuote += 1
        tutte.extend(merged)
        await _pubblica(
            job_id,
            EVENTO_ZONA,
            {
                "zona_id": zona.id,
                "extracted": len(merged),
                "from_regex": len(regex_hits),
                "from_seed": len(seed_hits),
                "from_llm": len(llm_hits),
                "failed": failed,
            },
        )

    livello = LivelloAncoreResult(segnali=[], ancore=tutte)
    esito = EstrazioneAncoreResult(
        livello=livello,
        n_zone=len(items),
        n_ancore=len(tutte),
        n_llm_failures=n_llm_failures,
        n_zone_vuote=n_zone_vuote,
    )
    await _pubblica(
        job_id,
        EVENTO_DOCUMENTO,
        {
            "n_zone": esito.n_zone,
            "n_ancore": esito.n_ancore,
            "n_llm_failures": esito.n_llm_failures,
            "n_zone_vuote": esito.n_zone_vuote,
        },
    )
    return esito


__all__ = [
    "AncoreZonaLlm",
    "EVENTO_DOCUMENTO",
    "EVENTO_ZONA",
    "EstrazioneAncoreResult",
    "MAX_CHAR_TESTO_ZONA",
    "STAGE",
    "SYSTEM_ANCORE_ESTRAZIONE",
    "estrai_ancore",
    "prepass_regex",
    "proposte_da_semi",
    "unisci_proposte",
    "user_ancore_zona",
]
