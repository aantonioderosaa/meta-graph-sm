"""D3 — un riassunto di transizione per ogni coppia di zone ordinale-adiacenti.

Indipendente dal classificatore macro (zona_edges.py): questo arco esiste
sempre, per ogni confine, e porta solo testo, mai un tipo di relazione.
Il riassunto di transizione è best-effort: se l'LLM manca, l'arco resta
comunque, con testo vuoto.
"""

from __future__ import annotations

from typing import Any

from app.models.event_graph import TransizioneZonaResult
from app.pipeline.event_graph.infra.llm import call_structured
from app.pipeline.event_graph.zona_segmentation import Zona

REGOLA = "zona_transizioni.genera"

SYSTEM_TRANSIZIONE = """Leggi i riassunti di due porzioni consecutive di una
narrazione. Scrivi 1-2 frasi su COSA CAMBIA nel contesto passando dalla prima
alla seconda (nuovi personaggi, nuovo luogo, nuova fase dell'azione, nuovo
argomento). Non riassumere il contenuto, descrivi il cambiamento. Italiano o
inglese, la stessa lingua del testo. Temperatura 0."""


_MAX_BLOCCO_TRANSIZIONE = 600


def _blocco_transizione(zona: Zona) -> str:
    """Riassunto if present; otherwise a truncated zone text so the trunk
    still gets a label when M1 failed."""
    riassunto = (zona.riassunto or "").strip()
    if riassunto:
        return riassunto
    testo = (zona.testo or "").strip()
    if len(testo) <= _MAX_BLOCCO_TRANSIZIONE:
        return testo
    return testo[:_MAX_BLOCCO_TRANSIZIONE].rstrip()


def user_transizione(zona_a: Zona, zona_b: Zona) -> str:
    """Prefer zone summaries; fall back to truncated testo if M1 left them empty."""
    return f"{_blocco_transizione(zona_a)}\n\n{_blocco_transizione(zona_b)}"


def _as_transizione(parsed: Any) -> TransizioneZonaResult | None:
    if isinstance(parsed, TransizioneZonaResult):
        return parsed
    try:
        return TransizioneZonaResult.model_validate(parsed)
    except Exception:
        return None


async def genera_transizione(
    zona_a: Zona,
    zona_b: Zona,
    *,
    job_id: str | None = None,
) -> TransizioneZonaResult | None:
    """Best-effort transition summary. Never raises."""
    try:
        parsed = await call_structured(
            SYSTEM_TRANSIZIONE,
            user_transizione(zona_a, zona_b),
            TransizioneZonaResult,
            temperature=0,
            job_id=job_id,
        )
    except Exception:
        return None
    return _as_transizione(parsed)


async def genera_transizioni_zona(
    zone: list[Zona],
    *,
    job_id: str | None = None,
) -> dict[tuple[str, str], str]:
    """One SUCCESSIONE_ZONA pair per consecutive expanded zones (ordinale).

    The arc is structural: every such pair is in the result even if the
    transition LLM fails or both riassunti are empty. Values are the
    transition text when the call succeeds, otherwise ``""``.
    """
    out: dict[tuple[str, str], str] = {}
    try:
        items = sorted(list(zone or []), key=lambda item: item.ordinale)
    except Exception:
        return out
    for i in range(len(items) - 1):
        try:
            zona_a, zona_b = items[i], items[i + 1]
            if not (zona_a.espansa and zona_b.espansa):
                continue
            if not zona_a.id or not zona_b.id or zona_a.id == zona_b.id:
                continue
            key = (zona_a.id, zona_b.id)
            out[key] = ""
            if not _blocco_transizione(zona_a) or not _blocco_transizione(zona_b):
                continue
            result = await genera_transizione(zona_a, zona_b, job_id=job_id)
            if result is None:
                continue
            riassunto = (result.riassunto or "").strip()
            if riassunto:
                out[key] = riassunto
        except Exception:
            continue
    return out


__all__ = [
    "REGOLA",
    "SYSTEM_TRANSIZIONE",
    "genera_transizione",
    "genera_transizioni_zona",
    "user_transizione",
]
