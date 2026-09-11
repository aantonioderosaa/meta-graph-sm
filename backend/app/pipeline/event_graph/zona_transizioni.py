"""D3 — un riassunto di transizione per ogni coppia di zone ordinale-adiacenti.

Indipendente dal classificatore macro (zona_edges.py): questo arco esiste
sempre, per ogni confine, e porta solo testo, mai un tipo di relazione.
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


def user_transizione(zona_a: Zona, zona_b: Zona) -> str:
    """Only zone summaries — never the raw zone text."""
    return f"{zona_a.riassunto}\n\n{zona_b.riassunto}"


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
    """One call per consecutive pair (zona[i], zona[i+1]) where BOTH have espansa=True.

    Best-effort per pair: LLM failure on one pair omits that key; other pairs
    continue. Keys are (zona_a.id, zona_b.id); values are non-empty riassunto
    strings. Skip pairs with empty riassunto on either side (no LLM call).
    """
    out: dict[tuple[str, str], str] = {}
    try:
        items = list(zone or [])
    except Exception:
        return out
    for i in range(len(items) - 1):
        try:
            zona_a, zona_b = items[i], items[i + 1]
            if not (zona_a.espansa and zona_b.espansa):
                continue
            if not (zona_a.riassunto or "").strip() or not (zona_b.riassunto or "").strip():
                continue
            result = await genera_transizione(zona_a, zona_b, job_id=job_id)
            if result is None:
                continue
            riassunto = (result.riassunto or "").strip()
            if not riassunto:
                continue
            out[(zona_a.id, zona_b.id)] = riassunto
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
