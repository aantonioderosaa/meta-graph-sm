"""Shared candidates-by-shared-entity helper (Addendum 2 M-nonadj).

One way to find candidates, three consumers: dedup (2), non-adjacent
relations (4), temporal placement (5). Does not classify pairs, does not
implement Allen, and does not change the Fusione/Successione/Catena table.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.models.event_graph import EventoRisolto

_SOGG_OGG = frozenset({"SOGG", "OGG"})


def mention_ids(evento: EventoRisolto) -> set[str]:
    """All argument mention ids (any role), plus forma_canonica tokens if present."""
    ids = {arg.menzione_id for arg in evento.argomenti if arg.menzione_id}
    ids.update(_forme_normalizzate(evento))
    return ids


def mention_ids_sogg_ogg(evento: EventoRisolto) -> set[str]:
    """SOGG/OGG mention ids only — the §10 overlap used by event coref / dedup."""
    return {
        arg.menzione_id
        for arg in evento.argomenti
        if arg.ruolo in _SOGG_OGG and arg.menzione_id
    }


def condividono_entita(a: EventoRisolto, b: EventoRisolto) -> bool:
    """True if mention id sets overlap OR normalized proper-name / forma strings overlap."""
    if mention_ids(a) & mention_ids(b):
        return True
    return bool(_forme_normalizzate(a) & _forme_normalizzate(b))


def seleziona_per_entita(
    nuovo: EventoRisolto,
    pool: Sequence[EventoRisolto],
    *,
    max_n: int | None = None,
) -> list[EventoRisolto]:
    """Events in pool that share an entity with nuovo, excluding self and fuso_in."""
    out: list[EventoRisolto] = []
    for event in pool:
        if event is nuovo:
            continue
        if nuovo.id and event.id == nuovo.id:
            continue
        if event.fuso_in:
            continue
        if not condividono_entita(nuovo, event):
            continue
        out.append(event)
    if max_n is None:
        return out
    return out[: max(0, int(max_n))]


def _forme_normalizzate(evento: EventoRisolto) -> set[str]:
    tokens: set[str] = set()
    for raw in _forme_grezze(evento):
        tokens.update(_norm_name_tokens(raw))
    return tokens


def _forme_grezze(evento: EventoRisolto) -> list[str]:
    found: list[str] = []
    for attr in ("forma_canonica", "forma"):
        value = getattr(evento, attr, None)
        if isinstance(value, str) and value.strip():
            found.append(value)
    for arg in evento.argomenti:
        for attr in ("forma_canonica", "forma"):
            value = getattr(arg, attr, None)
            if isinstance(value, str) and value.strip():
                found.append(value)
    return found


def _norm_name_tokens(text: str) -> set[str]:
    folded = " ".join(text.casefold().split())
    if not folded:
        return set()
    tokens = set(folded.split())
    tokens.add(folded)
    return tokens


__all__ = [
    "condividono_entita",
    "mention_ids",
    "mention_ids_sogg_ogg",
    "seleziona_per_entita",
]
