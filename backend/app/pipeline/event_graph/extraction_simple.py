"""Event extraction — legacy-equivalent, per-zone (Addendum 4).

Replaces the per-sentence grammatical extraction (``extraction.estrai_frase``)
as the source of events. Reproduces ``app.pipeline.node_extraction.
extract_event_entities`` — the branch the user identified as having the
cleanest event/participant resolution — inside ``event_graph``'s own
isolation boundary (D6): own model, own ``call_structured``, no import from
``app.core.*`` / ``app.pipeline.*`` outside this package.

One structured call per zone ("chunk"). Each returned ``event`` is an exact
substring of the source (list numbers and temporal prefixes included).
Participants go in ``entities`` (SOGG/OGG); temporal locators go in ``tempo``
(dictionary TEMPO), same status as a subject or object.

The LLM schema is flat lists of strings (bare heads). Nested
``{name, summary}`` objects break structured-output on local OpenAI-compatible
servers; summaries are derived in ``entita_forma.contesto_entita``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.event_graph import EventEntityExtractionResult
from app.pipeline.event_graph.infra.llm import call_structured

SYSTEM_EVENT_ENTITIES = (
    "Sei un assistente che risponde sempre con un oggetto JSON valido, senza spiegazioni."
)

USER_EVENT_ENTITIES_TEMPLATE = (
    "Analizza e riassumi le relazioni di partecipazione tra gli eventi e le entità "
    "nel passaggio. Ogni evento è una singola frase indipendente.\n"
    "Il campo `event` DEVE essere una sottostringa esatta del testo. "
    "Includi il numero di lista (es. \"1.\", \"11.\") e TUTTE le date, ore ed "
    "espressioni temporali sulla stessa riga (es. \"12 marzo 1987, ore 08:15 —\" "
    "o \"Dopo circa 3 ore, ore 11:20 —\"). Non tagliare quel prefisso: fa parte "
    "dell'evento, non è un titolo da scartare.\n"
    "Identifica i partecipanti in `entities` (soggetti, oggetti, luoghi-partecipanti). "
    "NON metterci date, ore o locuzioni temporali.\n"
    "Ogni voce in `entities` e in `tempo` è una STRINGA: solo la testa grammaticale, "
    "nuda, niente articoli, aggettivi, numeri, avverbi, dimostrativi. "
    "\"lo stesso vecchio garage\" → \"garage\". "
    "\"un giovane meccanico\" → \"meccanico\". "
    "\"una vecchia automobile abbandonata\" → \"automobile\". "
    "Due occorrenze con la stessa testa (anche se nel contesto significano "
    "cose diverse) sono LA STESSA entità.\n"
    "NON creare mai un'entità che sia solo un aggettivo, un numero, un avverbio "
    "o un dimostrativo. Quei pezzi restano nel testo attorno alla testa.\n"
    "Identifica in `tempo` (scompartimento TEMPO del dizionario) SOLO entità "
    "temporali standalone: orari (alle 18, alle otto, mezzogiorno, "
    "ore 08:15), date (12 marzo 1987, dodici marzo millenovecentottantasette, "
    "1843, dal 12 al 15 marzo), scadenze "
    "(entro il 31 marzo, tra 20 minuti) e espressioni vaghe lessicali "
    "(anni 70, dopo un po', ieri, 10 giorni fa, vigilia, Natale). Ogni voce in "
    "`tempo` è un'entità e deve comparire anche dentro `event`. Per il TEMPO, "
    "i numeri che FANNO parte della data/ora restano nella stringa "
    "(\"12 marzo 1987\", \"ore 08:15\"); articoli e aggettivi no "
    "(\"il freddo 12 marzo\" → \"12 marzo\"). "
    "NON creare entità con la sintassi della "
    "frase: vietato \"dopo circa 3 ore\", \"tre ore dopo\", \"prima di due "
    "giorni\" — sono complementi, non nodi. Lo scarto va lasciato nel testo "
    "dell'evento, non in `tempo`.\n"
    "Non usare puntini di sospensione.\n"
    "Non creare relazioni entità–entità per sola co-presenza nella stessa lista.\n"
    'Restituisci un oggetto JSON: {{"participations": [{{"event": "...", '
    '"entities": ["...", "..."], '
    '"tempo": ["...", "..."]}}, ...]}}\n\n'
    'Testo:\n"""{chunk_text}"""'
)


def _nome_da_voce(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("name") or item.get("summary") or "").strip()
    name = getattr(item, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return str(item).strip() if item is not None else ""


class EventEntityParticipationLLM(BaseModel):
    """Flat participation row for structured output (strings only)."""

    event: str
    entities: list[str] = Field(default_factory=list)
    tempo: list[str] = Field(default_factory=list)

    @field_validator("entities", "tempo", mode="before")
    @classmethod
    def _nomi(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [nome for item in value if (nome := _nome_da_voce(item))]


class EventEntityExtractionLLM(BaseModel):
    participations: list[EventEntityParticipationLLM] = Field(default_factory=list)


async def extract_event_entities(
    chunk_text: str, job_id: str | None = None
) -> EventEntityExtractionResult:
    """One structured call: independent-clause events + participants + TEMPO."""
    raw = await call_structured(
        SYSTEM_EVENT_ENTITIES,
        USER_EVENT_ENTITIES_TEMPLATE.format(chunk_text=chunk_text),
        EventEntityExtractionLLM,
        temperature=0,
        job_id=job_id,
    )
    return EventEntityExtractionResult.model_validate(raw.model_dump())


__all__ = [
    "SYSTEM_EVENT_ENTITIES",
    "USER_EVENT_ENTITIES_TEMPLATE",
    "EventEntityExtractionLLM",
    "extract_event_entities",
]
