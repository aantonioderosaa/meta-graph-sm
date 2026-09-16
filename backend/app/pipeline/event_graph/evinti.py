"""Inferred events: facts evinced from extracted nodes, not written in the chunk.

Not the same pass as ``buchi``. ``buchi`` fills leftover *sentences already
in* ``zona.testo``. This module would add assertions the text never states
but that follow from events already on the graph (elided result, implicit
cause, skipped beat).

Not designed yet. The pipeline calls ``evinci_eventi_documento`` after
the exposition railway (buchi sits immediately before SEQUENZA) and
before L3. Until then this is a no-op.

Isolation D6: no ``app.core.*``, no LLM until the design lands.
"""

from __future__ import annotations

from typing import Any

from app.models.event_graph import EventoRisolto, SottoGrafo
from app.pipeline.event_graph.zona_segmentation import Zona

REGOLA = "evinti.non_implementato"


async def evinci_eventi_documento(
    sotto: SottoGrafo,
    zone: list[Zona],
    *,
    job_id: str | None = None,
    session: Any = None,
) -> list[EventoRisolto]:
    """Placeholder. Returns no events. Does not persist."""
    del sotto, zone, job_id, session
    return []


__all__ = [
    "REGOLA",
    "evinci_eventi_documento",
]
