"""Prompt adattati da autoschemakg atlas_rag/llm_generator/prompt/triple_extraction_prompt.py
(TRIPLE_INSTRUCTIONS['en'], CONCEPT_INSTRUCTIONS['en'] — MIT). Contenuto semantico ed
esempi few-shot invariati; adattato solo l'involucro JSON (oggetto con chiave, non
array nudo) per client.beta.chat.completions.parse (vedi app/core/llm_client.py)."""

from __future__ import annotations

ENTITY_RELATION_SYSTEM_PROMPT = (
    "Sei un assistente che risponde sempre con un oggetto JSON valido, senza spiegazioni."
)

EVENT_ENTITY_SYSTEM_PROMPT = ENTITY_RELATION_SYSTEM_PROMPT
EVENT_RELATION_SYSTEM_PROMPT = ENTITY_RELATION_SYSTEM_PROMPT
EVENT_CONCEPT_SYSTEM_PROMPT = ENTITY_RELATION_SYSTEM_PROMPT
ENTITY_CONCEPT_SYSTEM_PROMPT = ENTITY_RELATION_SYSTEM_PROMPT

ENTITY_RELATION_USER_PROMPT_TEMPLATE = (
    "Dato un passaggio, riassumi in modo conciso tutte le entità importanti e le "
    "relazioni tra loro. Le relazioni devono catturare brevemente le connessioni tra "
    "entità, senza ripetere informazioni già in head/tail. Le entità devono essere il "
    "più specifiche possibile. Escludi i pronomi come entità.\n"
    'Restituisci un oggetto JSON: {{"triples": [{{"head": "...", "relation": "...", '
    '"tail": "..."}}, ...]}}\n\n'
    'Testo:\n"""{chunk_text}"""'
)

EVENT_ENTITY_USER_PROMPT_TEMPLATE = (
    "Analizza e riassumi le relazioni di partecipazione tra gli eventi e le entità "
    "nel passaggio. Ogni evento è una singola frase indipendente. Identifica tutte "
    "le entità che hanno partecipato. Non usare puntini di sospensione.\n"
    'Restituisci un oggetto JSON: {{"participations": [{{"event": "...", '
    '"entities": ["...", "..."]}}, ...]}}\n\n'
    'Testo:\n"""{chunk_text}"""'
)

EVENT_RELATION_USER_PROMPT_TEMPLATE = (
    "Analizza e riassumi le relazioni tra gli eventi nel passaggio. Ogni evento è "
    "una singola frase indipendente. Identifica relazioni temporali e causali tra "
    "gli eventi usando i seguenti tipi: before, after, at the same time, because, "
    "and as a result. Ogni tripla estratta deve essere specifica, significativa e "
    "autonoma. Non usare puntini di sospensione.\n"
    'Restituisci un oggetto JSON: {{"triples": [{{"head": "...", "relation": "...", '
    '"tail": "..."}}, ...]}}\n\n'
    'Testo:\n"""{chunk_text}"""'
)

EVENT_CONCEPT_USER_PROMPT_TEMPLATE = (
    "I will give you an EVENT. You need to give several phrases containing 1-2 words "
    "for the ABSTRACT EVENT of this EVENT.\n"
    'You must return your answer as a JSON object: {{"concepts": ["...", "..."]}}\n'
    "You can't return anything other than answers.\n"
    "These abstract event words should fulfill the following requirements.\n"
    "1. The ABSTRACT EVENT phrases can well represent the EVENT, and it could be the "
    "type of the EVENT or the related concepts of the EVENT.\n"
    "2. Strictly follow the provided format, do not add extra characters or words.\n"
    "3. Write at least 3 or more phrases at different abstract level if possible.\n"
    "4. Do not repeat the same word and the input in the answer.\n"
    "5. Stop immediately if you can't think of any more phrases, and no explanation is needed.\n"
    "\n"
    "EVENT: A man retreats to mountains and forests.\n"
    "Your answer: retreat, relaxation, escape, nature, solitude\n"
    "EVENT: A cat chased a prey into its shelter\n"
    "Your answer: hunting, escape, predation, hidding, stalking\n"
    "EVENT: Sam playing with his dog\n"
    "Your answer: relaxing event, petting, playing, bonding, friendship\n"
    "EVENT: {event_text}\n"
    "Your answer:"
)

ENTITY_CONCEPT_USER_PROMPT_TEMPLATE = (
    "I will give you an ENTITY. You need to give several phrases containing 1-2 words "
    "for the ABSTRACT ENTITY of this ENTITY.\n"
    'You must return your answer as a JSON object: {{"concepts": ["...", "..."]}}\n'
    "You can't return anything other than answers.\n"
    "These abstract intention words should fulfill the following requirements.\n"
    "1. The ABSTRACT ENTITY phrases can well represent the ENTITY, and it could be the "
    "type of the ENTITY or the related concepts of the ENTITY.\n"
    "2. Strictly follow the provided format, do not add extra characters or words.\n"
    "3. Write at least 3 or more phrases at different abstract level if possible.\n"
    "4. Do not repeat the same word and the input in the answer.\n"
    "5. Stop immediately if you can't think of any more phrases, and no explanation is needed.\n"
    "\n"
    "ENTITY: Soul\n"
    "CONTEXT: premiered BFI London Film Festival, became highest-grossing Pixar release\n"
    "Your answer: movie, film\n"
    "\n"
    "ENTITY: Thinkpad X60\n"
    "CONTEXT: Richard Stallman announced he is using Trisquel on a Thinkpad X60\n"
    "Your answer: Thinkpad, laptop, machine, device, hardware, computer, brand\n"
    "\n"
    "ENTITY: Harry Callahan\n"
    "CONTEXT: bluffs another robber, tortures Scorpio\n"
    "Your answer: person, Amarican, character, police officer, detective\n"
    "\n"
    "ENTITY: Black Mountain College\n"
    "CONTEXT: was started by John Andrew Rice, attracted faculty\n"
    "Your answer: college, university, school, liberal arts college\n"
    "\n"
    "EVENT: 1st April\n"
    "CONTEXT: Utkal Dibas celebrates\n"
    "Your answer: date, day, time, festival\n"
    "\n"
    "ENTITY: {entity_name}\n"
    "CONTEXT: {context}\n"
    "Your answer:"
)


def build_entity_relation_prompt(chunk_text: str) -> tuple[str, str]:
    return ENTITY_RELATION_SYSTEM_PROMPT, ENTITY_RELATION_USER_PROMPT_TEMPLATE.format(
        chunk_text=chunk_text
    )


def build_event_entity_prompt(chunk_text: str) -> tuple[str, str]:
    return EVENT_ENTITY_SYSTEM_PROMPT, EVENT_ENTITY_USER_PROMPT_TEMPLATE.format(
        chunk_text=chunk_text
    )


def build_event_relation_prompt(chunk_text: str) -> tuple[str, str]:
    return EVENT_RELATION_SYSTEM_PROMPT, EVENT_RELATION_USER_PROMPT_TEMPLATE.format(
        chunk_text=chunk_text
    )


def build_event_concept_prompt(event_text: str) -> tuple[str, str]:
    return EVENT_CONCEPT_SYSTEM_PROMPT, EVENT_CONCEPT_USER_PROMPT_TEMPLATE.format(
        event_text=event_text
    )


def build_entity_concept_prompt(entity_name: str, context: str) -> tuple[str, str]:
    return ENTITY_CONCEPT_SYSTEM_PROMPT, ENTITY_CONCEPT_USER_PROMPT_TEMPLATE.format(
        entity_name=entity_name,
        context=context,
    )
