"""NL → EventQuerySpec compiler (piano sez. 15.4 / M18).

Compiles free text into the same EventQuerySpec used by the structured tab,
then executes with query_structured.esegui. No Cypher, PPR, or embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.models.event_graph import EventQuerySpec
from app.pipeline.event_graph.answer import sintetizza_risposta
from app.pipeline.event_graph.infra.llm import call_structured
from app.pipeline.event_graph.query_nl_prompts import SYSTEM_NL_QUERY, user_nl_query
from app.pipeline.event_graph.query_structured import QueryResult, esegui, registra_query


@dataclass
class NlQueryOutcome:
    spec: EventQuerySpec
    risultato: QueryResult
    stored_id: str
    risposta: str | None = None
    eventi_citati: list[str] = field(default_factory=list)


def _as_spec(parsed: Any) -> EventQuerySpec:
    if isinstance(parsed, EventQuerySpec):
        return parsed
    try:
        return EventQuerySpec.model_validate(parsed)
    except ValidationError:
        raise


async def compila(
    testo: str,
    *,
    call_structured: Any = None,
    job_id: str | None = None,
) -> EventQuerySpec:
    """NL → EventQuerySpec via call_structured. No Cypher here."""
    if not (testo or "").strip():
        raise ValueError("testo is required")
    llm = (
        call_structured
        if call_structured is not None
        else globals()["call_structured"]
    )
    parsed = await llm(
        SYSTEM_NL_QUERY,
        user_nl_query(testo),
        EventQuerySpec,
        temperature=0,
        job_id=job_id,
    )
    return _as_spec(parsed)


async def esegui_nl(
    session: Any,
    testo: str,
    *,
    call_structured: Any = None,
    sintetizza: Any = None,
    job_id: str | None = None,
) -> NlQueryOutcome:
    """compila then query_structured.esegui, then a best-effort answer.

    ``sintetizza`` — if given — replaces ``answer.sintetizza_risposta`` (same
    ``(testo, risultato, job_id=...)`` signature), for tests/injection. The
    synthesis step never raises: a failure just leaves ``risposta`` as
    ``None``, it never fails the query itself.
    """
    spec = await compila(testo, call_structured=call_structured, job_id=job_id)
    risultato = await esegui(session, spec)
    stored = registra_query(spec, risultato, modo="nl")
    sintesi_fn = sintetizza if sintetizza is not None else sintetizza_risposta
    try:
        sintesi = await sintesi_fn(testo, risultato, job_id=job_id)
    except Exception:
        sintesi = None
    risposta = sintesi.risposta if sintesi is not None else None
    eventi_citati = list(sintesi.eventi_citati) if sintesi is not None else []
    return NlQueryOutcome(
        spec=spec,
        risultato=risultato,
        stored_id=stored.id,
        risposta=risposta,
        eventi_citati=eventi_citati,
    )


__all__ = [
    "NlQueryOutcome",
    "compila",
    "esegui_nl",
]
