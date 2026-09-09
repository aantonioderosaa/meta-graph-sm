"""Event extraction — legacy-equivalent, per-zone (Addendum 4).

Replaces the per-sentence grammatical extraction (``extraction.estrai_frase``)
as the source of events. Reproduces ``app.pipeline.node_extraction.
extract_event_entities`` — the branch the user identified as having the
cleanest event/participant resolution — inside ``event_graph``'s own
isolation boundary (D6): own model, own ``call_structured``, no import from
``app.core.*`` / ``app.pipeline.*`` outside this package.

One structured call per zone ("chunk"). Each returned ``event`` is documented
to the model as a single independent sentence; the model also resolves which
named entities participated. No further grammatical decomposition (tempo,
segmentazione, SOGG/OGG roles, ...) is attempted — ``dedup.py`` fills those
with neutral defaults so the existing relation mechanism (event_edges /
sentence_pair_linking / chiusura_temporale) keeps working unchanged.
"""

from __future__ import annotations

from app.models.event_graph import EventEntityExtractionResult
from app.pipeline.event_graph.infra.llm import call_structured

SYSTEM_EVENT_ENTITIES = (
    "Sei un assistente che risponde sempre con un oggetto JSON valido, senza spiegazioni."
)

USER_EVENT_ENTITIES_TEMPLATE = (
    "Analizza e riassumi le relazioni di partecipazione tra gli eventi e le entità "
    "nel passaggio. Ogni evento è una singola frase indipendente. Identifica tutte "
    "le entità che hanno partecipato. Non usare puntini di sospensione.\n"
    "Non creare relazioni entità–entità per sola co-presenza nella stessa lista.\n"
    'Restituisci un oggetto JSON: {{"participations": [{{"event": "...", '
    '"entities": ["...", "..."]}}, ...]}}\n\n'
    'Testo:\n"""{chunk_text}"""'
)


async def extract_event_entities(
    chunk_text: str, job_id: str | None = None
) -> EventEntityExtractionResult:
    """One structured call: independent-clause events + participant entities."""
    return await call_structured(
        SYSTEM_EVENT_ENTITIES,
        USER_EVENT_ENTITIES_TEMPLATE.format(chunk_text=chunk_text),
        EventEntityExtractionResult,
        temperature=0,
        job_id=job_id,
    )


__all__ = [
    "SYSTEM_EVENT_ENTITIES",
    "USER_EVENT_ENTITIES_TEMPLATE",
    "extract_event_entities",
]
