"""Answer synthesis over retrieved events (Addendum 5).

Takes the original NL question plus what ``query_structured.esegui`` already
retrieved (events in exposition order, plus arcs) and asks the model for a
short answer grounded only in those facts — no second retrieval, no Cypher,
no new search here. Same isolation as ``query_nl.py``.

Best-effort by design: ``sintetizza_risposta`` never raises. A failed or
unreachable LLM degrades to ``None`` (the caller still has the raw
``eventi``/``archi`` from retrieval, which is not affected).
"""

from __future__ import annotations

from typing import Any

from app.models.event_graph import RispostaSintetizzata
from app.pipeline.event_graph.infra.llm import call_structured
from app.pipeline.event_graph.query_structured import QueryResult

SYSTEM_ANSWER = """You answer a question using ONLY the events listed below,
already given in exposition order (the order the story tells them — not
necessarily real-world chronology). Do not invent facts beyond them, do not
use outside knowledge. If the events do not support an answer, say so
plainly instead of guessing.

List, in eventi_citati, the ids of the events you actually used to build the
answer (a subset of the ids given — never an invented id).

Answer in the same language as the question. Temperature is 0. Never call
tools, never open a tool loop — one structured answer."""


def _evento_riga(evento: dict) -> str:
    parts = [f"id={evento.get('id')}"]
    lemma = evento.get("lemma")
    if lemma:
        parts.append(f'testo="{lemma}"')
    for campo in ("fattualita", "tempo", "documento"):
        valore = evento.get(campo)
        if valore:
            parts.append(f"{campo}={valore}")
    return " | ".join(parts)


def _archi_riga(arco: dict) -> str:
    tipo = arco.get("tipo") or arco.get("type") or "?"
    da = arco.get("da_id", arco.get("source", "?"))
    a = arco.get("a_id", arco.get("target", "?"))
    return f"{da} -{tipo}-> {a}"


def user_answer(testo: str, risultato: QueryResult) -> str:
    eventi = list(getattr(risultato, "eventi", None) or [])
    archi = list(getattr(risultato, "archi", None) or [])
    righe_eventi = "\n".join(_evento_riga(e) for e in eventi) or "(nessuno)"
    righe_archi = "\n".join(_archi_riga(a) for a in archi) or "(nessuno)"
    return (
        f"Domanda / question: {testo}\n\n"
        f"Eventi recuperati, in ordine di esposizione:\n{righe_eventi}\n\n"
        f"Archi recuperati:\n{righe_archi}\n"
    )


def _as_risposta(parsed: Any) -> RispostaSintetizzata | None:
    if isinstance(parsed, RispostaSintetizzata):
        return parsed
    try:
        return RispostaSintetizzata.model_validate(parsed)
    except Exception:
        return None


async def sintetizza_risposta(
    testo: str,
    risultato: QueryResult,
    *,
    job_id: str | None = None,
) -> RispostaSintetizzata | None:
    """Best-effort answer over already-retrieved events. Never raises."""
    eventi = list(getattr(risultato, "eventi", None) or [])
    if not eventi:
        return RispostaSintetizzata(
            risposta="Nessun evento recuperato per rispondere a questa domanda.",
            eventi_citati=[],
        )
    try:
        parsed = await call_structured(
            SYSTEM_ANSWER,
            user_answer(testo, risultato),
            RispostaSintetizzata,
            temperature=0,
            job_id=job_id,
        )
    except Exception:
        return None
    return _as_risposta(parsed)


__all__ = ["SYSTEM_ANSWER", "sintetizza_risposta", "user_answer"]
