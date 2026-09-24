"""Standalone temporal entities: date, clock, deadline, or vague — never syntax.

A TEMPO mention is an entity only when it names a placement. Phrasal offsets
such as ``dopo circa 3 ore`` are sentence syntax, not nodes. Vague lexical
spans (``anni 70``, ``dopo un po'``) stay as tipo ``vaga`` so they can
contextualise without minting a calendar.

Isolation D6: ``app.pipeline.event_graph.*`` and ``app.models.event_graph``.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from app.models.event_graph import TIPI_ANCORA, TipoAncora
from app.pipeline.event_graph.tempo_iso import (
    analizza,
    collocazione_da_espressione,
    intervallo_da_espressione,
    orario_da_espressione,
)

TIPI_ENTITA_TEMPORALE: tuple[TipoAncora, ...] = ("data", "ora", "scadenza", "vaga")

_UNITA_DURATA = (
    r"minuti|minuto|minutes?|minute|ore|ora|hours?|hour|"
    r"secondi|secondo|seconds?|second|giorni|giorno|days?|day|"
    r"settimane|settimana|weeks?|week|mesi|mese|months?|month|"
    r"anni|anno|years?|year"
)
_NUMERO = (
    r"\d+|[A-Za-zÀ-ÿ]+"
)
_OFFSET_QUANTIFICATO = re.compile(
    rf"^\s*(?:(?:dopo|prima|tra|in|after|before|within)"
    rf"(?:\s+(?:di|del|dello|della|the))?\s+)?"
    rf"(?:circa|about|quasi|almost|roughly)?\s*"
    rf"(?:{_NUMERO})\s+(?:{_UNITA_DURATA})"
    rf"(?:\s+e\s+mezzo)?"
    rf"(?:\s+(?:dopo|prima|later|ago|after|before))?\s*$",
    re.IGNORECASE,
)
_DURATA_NUDA = re.compile(
    rf"^\s*(?:circa|about|quasi|almost)?\s*"
    rf"(?:{_NUMERO})\s+(?:{_UNITA_DURATA})(?:\s+e\s+mezzo)?\s*$",
    re.IGNORECASE,
)
_SCADENZA_RELATIVA = re.compile(
    rf"^\s*(?:tra|fra|in|entro|within)\s+"
    rf"(?:circa|about|quasi|almost|roughly)?\s*"
    rf"(?:{_NUMERO})\s+(?:{_UNITA_DURATA})"
    rf"(?:\s+e\s+mezzo)?\s*$",
    re.IGNORECASE,
)
_PASSATO_RELATIVO = re.compile(
    rf"^\s*(?:circa|about|quasi|almost|roughly)?\s*"
    rf"(?:{_NUMERO})\s+(?:{_UNITA_DURATA})"
    rf"(?:\s+e\s+mezzo)?\s+(?:fa|ago)\s*$",
    re.IGNORECASE,
)
_SCADENZA = re.compile(
    r"^\s*(?:entro(?:\s+(?:il|la|lo|l['’]|the))?|scadenza(?:\s+del)?|"
    r"deadline|due(?:\s+(?:by|on))?|by|until|fino\s+al)\b",
    re.IGNORECASE,
)
_DECENNIO = re.compile(
    r"^\s*(?:(?:negli|nei|in|the)\s+)?anni\s+['’]?\d0(?:s)?"
    r"|^\s*(?:the\s+)?['’]?\d0s"
    r"|^\s*anni\s+(?:venti|trenta|quaranta|cinquanta|sessanta|"
    r"settanta|ottanta|novanta)\s*$",
    re.IGNORECASE,
)
_VAGA_LESSICALE = re.compile(
    r"^\s*(?:"
    r"dopo\s+un\s+po['’]?|dopo\s+poco|after\s+a\s+while|after\s+a\s+bit|"
    r"un\s+po['’]?\s+dopo|poco\s+dopo|"
    r"ieri|oggi|domani|yesterday|today|tomorrow|"
    r"(?:questa|quel(?:la)?|la)\s+sera|(?:this|that)\s+(?:evening|night)|"
    r"vigilia(?:\s+di\s+\w+)?|natale|christmas|"
    r"(?:quell['’]|questa|quella|un['’]?|the)\s+"
    r"(?:estate|inverno|primavera|autunno|pomeriggio|mattina|notte|sera|"
    r"summer|winter|spring|autumn|fall|afternoon|morning|night|evening)|"
    r"un\s+giorno|one\s+day|un\s+giorno\s+o\s+l['’]altro"
    r")\s*$",
    re.IGNORECASE,
)
_PREFISSO_SINTASSI = re.compile(
    r"^\s*(?:nel|nello|nella|nei|negli|in|di|del|dello|della|dei|degli|"
    r"il|lo|la|i|gli|le|on|during|around)\s+",
    re.IGNORECASE,
)
_VERBO_FRASALE = re.compile(
    r"\b(?:è|era|fu|furono|accadde|successe|happened|was|were|did|went|"
    r"quando|mentre|mentreché|after\s+he|after\s+she|dopo\s+che)\b",
    re.IGNORECASE,
)


class EntitaTemporale(NamedTuple):
    """Classified standalone temporal entity, or tipo is None when rejected."""

    tipo: TipoAncora | None
    forma: str


def _compatta(testo: str) -> str:
    return " ".join((testo or "").split())


def _e_offset_quantificato(span: str) -> bool:
    testo = _compatta(span)
    if not testo:
        return False
    if _OFFSET_QUANTIFICATO.fullmatch(testo) or _DURATA_NUDA.fullmatch(testo):
        return True
    return False


def _e_sintassi_frasale(span: str) -> bool:
    testo = _compatta(span)
    if not testo:
        return True
    if _VERBO_FRASALE.search(testo):
        return True
    if len(testo.split()) > 10 and intervallo_da_espressione(testo) is None:
        return True
    return False


def _nudo_data(span: str) -> str:
    testo = _compatta(span)
    precedente = None
    while precedente != testo:
        precedente = testo
        testo = _PREFISSO_SINTASSI.sub("", testo).strip()
    return testo


def classifica_entita_temporale(span: str | None) -> EntitaTemporale:
    """Return (tipo, standalone form) or tipo None when this is not an entity."""
    grezzo = _compatta(span or "")
    if not grezzo:
        return EntitaTemporale(None, "")
    if _DECENNIO.fullmatch(grezzo) or _VAGA_LESSICALE.fullmatch(grezzo):
        return EntitaTemporale("vaga", grezzo)
    if _SCADENZA_RELATIVA.fullmatch(grezzo):
        return EntitaTemporale("scadenza", grezzo)
    if _PASSATO_RELATIVO.fullmatch(grezzo):
        return EntitaTemporale("vaga", grezzo)
    if _e_offset_quantificato(grezzo):
        return EntitaTemporale(None, grezzo)
    if _e_sintassi_frasale(grezzo):
        return EntitaTemporale(None, grezzo)
    if _SCADENZA.match(grezzo):
        forma = grezzo if len(grezzo) <= 40 else grezzo[:40]
        return EntitaTemporale("scadenza", forma)
    orario = orario_da_espressione(grezzo)
    if orario is not None and analizza(grezzo) is None:
        if collocazione_da_espressione(grezzo) is None:
            return EntitaTemporale("ora", grezzo)

    nudo = _nudo_data(grezzo)
    if nudo and nudo != grezzo:
        if _e_offset_quantificato(nudo):
            return EntitaTemporale(None, grezzo)
        interno = classifica_entita_temporale(nudo)
        if interno.tipo in {"data", "ora", "scadenza"}:
            return interno
        if interno.tipo == "vaga":
            return interno

    if intervallo_da_espressione(grezzo) is not None:
        return EntitaTemporale("data", grezzo)
    if collocazione_da_espressione(grezzo) is not None or analizza(grezzo) is not None:
        return EntitaTemporale("data", nudo or grezzo)
    if nudo and nudo != grezzo:
        if intervallo_da_espressione(nudo) is not None:
            return EntitaTemporale("data", nudo)
        if collocazione_da_espressione(nudo) is not None or analizza(nudo) is not None:
            return EntitaTemporale("data", nudo)

    if _DECENNIO.fullmatch(nudo) or _VAGA_LESSICALE.fullmatch(nudo):
        return EntitaTemporale("vaga", nudo)

    if len(grezzo.split()) <= 4 and not _e_offset_quantificato(grezzo):
        return EntitaTemporale("vaga", grezzo)
    return EntitaTemporale(None, grezzo)


def forma_entita_temporale(span: str | None) -> str | None:
    """Standalone form if this is an allowed entity, else None."""
    entita = classifica_entita_temporale(span)
    if entita.tipo is None or entita.tipo not in TIPI_ANCORA:
        return None
    return entita.forma or None


def tipo_entita_temporale(span: str | None) -> TipoAncora | None:
    return classifica_entita_temporale(span).tipo


__all__ = [
    "TIPI_ENTITA_TEMPORALE",
    "EntitaTemporale",
    "classifica_entita_temporale",
    "forma_entita_temporale",
    "tipo_entita_temporale",
]
