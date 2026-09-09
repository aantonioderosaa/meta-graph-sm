"""Thin prompts for temporal agent B (fase 13). Isolation: event_graph only."""

from __future__ import annotations

from typing import Any

SYSTEM_TEMPORAL = """You place one NEW event relative to one CANDIDATE in chronological order.
Return only structured fields.
ordine ∈ {prima, dopo, sovrapposto, incerto}: how NEW relates to CANDIDATE
(prima = NEW happens earlier than CANDIDATE; dopo = NEW happens later).
Optional tempo_assoluto: ISO (YYYY / YYYY-MM / YYYY-MM-DD / datetime),
interval {da, a}, or symbolic {relativo_a, offset}.
Do not invent dates. English and Italian. Temperature is 0."""


def user_pair(
    nuovo_id: str,
    nuovo_lemma: str,
    nuovo_tempo: Any,
    nuovo_grezzo: str | None,
    candidato_id: str,
    candidato_lemma: str,
    candidato_tempo: Any,
    candidato_grezzo: str | None = None,
) -> str:
    return (
        f"NEW id={nuovo_id} lemma={nuovo_lemma!r} "
        f"tempo_assoluto={nuovo_tempo!r} grezzo={nuovo_grezzo!r}\n"
        f"CANDIDATE id={candidato_id} lemma={candidato_lemma!r} "
        f"tempo_assoluto={candidato_tempo!r} grezzo={candidato_grezzo!r}\n"
        "ordine = how NEW relates to CANDIDATE."
    )
