"""Prompt adattati da autoschemakg atlas_rag/llm_generator/prompt/triple_extraction_prompt.py
(TRIPLE_INSTRUCTIONS['en'], CONCEPT_INSTRUCTIONS['en'] — MIT). Contenuto semantico ed
esempi few-shot invariati per concetti; estrazione entità/relazioni in due passaggi
anti-blur (doc1 §2.4–§2.5, doc4 §6). Involucro JSON (oggetto con chiave, non array nudo)
per client.beta.chat.completions.parse (vedi app/core/llm_client.py)."""

from __future__ import annotations

from app.models.kernel import EntityKernelType, RelationKernelType
from app.pipeline.domain_book import GENRE_NOT_TOPIC_PROMPT

JSON_SYSTEM_PROMPT = (
    "Sei un assistente che risponde sempre con un oggetto JSON valido, senza spiegazioni."
)

ENTITY_LIST_SYSTEM_PROMPT = JSON_SYSTEM_PROMPT
PAIR_RELATION_SYSTEM_PROMPT = JSON_SYSTEM_PROMPT
CORPUS_SUMMARY_SYSTEM_PROMPT = JSON_SYSTEM_PROMPT
EVENT_ENTITY_SYSTEM_PROMPT = JSON_SYSTEM_PROMPT
EVENT_RELATION_SYSTEM_PROMPT = JSON_SYSTEM_PROMPT
EVENT_CONCEPT_SYSTEM_PROMPT = JSON_SYSTEM_PROMPT
ENTITY_CONCEPT_SYSTEM_PROMPT = JSON_SYSTEM_PROMPT

_ENTITY_KERNEL_LINES = "\n".join(
    f"- {member.value}" for member in EntityKernelType
)
_RELATION_KERNEL_LINES = "\n".join(
    f"- {member.value}" for member in RelationKernelType
)

ENTITY_LIST_USER_PROMPT_TEMPLATE = (
    "{genre_not_topic}\n\n"
    "Macro-riassunto del corpus (contesto; non elencare sottodomini):\n"
    '"""{corpus_summary}"""\n\n'
    "Dal passaggio sotto, estrai SOLO le entità importanti. Non estrarre relazioni. "
    "Ogni entità ha un nome, un breve summary in linguaggio naturale (chi/che cosa è, "
    "nel contesto del passaggio), e kernel_category pari a esattamente una delle "
    "categorie fondazionali E1–E8:\n"
    "{entity_kernel_list}\n"
    "Non combinare categorie. Non usare pronomi come entità. I nomi sono stringhe "
    "singole, mai liste.\n"
    'Restituisci un oggetto JSON: {{"entities": [{{"name": "...", "summary": "...", '
    '"kernel_category": "..."}}, ...]}}\n\n'
    'Testo:\n"""{chunk_text}"""'
)

PAIR_RELATION_USER_PROMPT_TEMPLATE = (
    "{genre_not_topic}\n\n"
    "Macro-riassunto del corpus (contesto; non elencare sottodomini):\n"
    '"""{corpus_summary}"""\n\n'
    "Decidi se esiste UNA relazione asserita tra le due entità seguenti, letti i loro "
    "summary insieme al passaggio. Questa è una decisione per coppia: non estrarre "
    "altre coppie, non raggruppare testimoni.\n\n"
    "Entità A: {name_a}\n"
    "Summary A: {summary_a}\n"
    "Entità B: {name_b}\n"
    "Summary B: {summary_b}\n\n"
    "Se i summary, letti insieme e con il passaggio, NON giustificano un legame, "
    "restituisci related=false.\n"
    "Se related=true, indica:\n"
    "- relation: raffinamento libero (es. coached_by, works_at)\n"
    "- kernel_parent: esattamente una delle 6 primitive R1–R6 (il raffinamento "
    "pende sotto una di queste, non è una settima primitiva):\n"
    "{relation_kernel_list}\n"
    "- witness_source e witness_target: stringhe non vuote (un testimone per lato; "
    "tipicamente i nomi o gli span che attestano A e B). Un fatto senza entrambi i "
    "testimoni non è rappresentabile.\n"
    "head e tail sono impliciti (A e B): non restituire liste di id.\n"
    'Restituisci un oggetto JSON: {{"related": true/false, "relation": "...", '
    '"kernel_parent": "...", "witness_source": "...", "witness_target": "..."}}\n\n'
    'Testo:\n"""{chunk_text}"""'
)

CORPUS_SUMMARY_USER_PROMPT_TEMPLATE = (
    "Aggiorna il riassunto macro del corpus in linguaggio naturale: di cosa tratta "
    "complessivamente il corpus finora, dopo l'aggiunta del nuovo documento.\n"
    "NON elencare i sottodomini in anticipo. NON inventare una nuova categoria del "
    "kernel. NON restituire una lista di argomenti. Solo un testo continuo.\n\n"
    "Riassunto esistente (può essere vuoto):\n"
    '"""{existing_summary}"""\n\n'
    "Nuovo documento:\n"
    '"""{document_text}"""\n\n'
    'Restituisci un oggetto JSON: {{"summary_text": "..."}}'
)

EVENT_ENTITY_USER_PROMPT_TEMPLATE = (
    "Analizza e riassumi le relazioni di partecipazione tra gli eventi e le entità "
    "nel passaggio. Ogni evento è una singola frase indipendente. Identifica tutte "
    "le entità che hanno partecipato. Non usare puntini di sospensione.\n"
    "Non creare relazioni entità–entità per sola co-presenza nella stessa lista.\n"
    'Restituisci un oggetto JSON: {{"participations": [{{"event": "...", '
    '"entities": ["...", "..."]}}, ...]}}\n\n'
    'Testo:\n"""{chunk_text}"""'
)

EVENT_RELATION_USER_PROMPT_TEMPLATE = (
    "Analizza e riassumi le relazioni tra gli eventi nel passaggio. Ogni evento è "
    "una singola frase indipendente. Identifica relazioni temporali e causali tra "
    "gli eventi usando raffinamenti come before, after, at the same time, because, "
    "and as a result. Ogni tripla estratta deve essere specifica, significativa e "
    "autonoma. Non usare puntini di sospensione.\n"
    "head e tail sono stringhe singole (un evento per lato), mai liste. Ogni fatto "
    "richiede witness_source e witness_target non vuoti, e kernel_parent pari a "
    "esattamente una delle 6 primitive R1–R6:\n"
    "{relation_kernel_list}\n"
    "(before/after/at the same time → Temporale; because/as a result → Causale.)\n"
    'Restituisci un oggetto JSON: {{"triples": [{{"head": "...", "tail": "...", '
    '"relation": "...", "kernel_parent": "...", "witness_source": "...", '
    '"witness_target": "..."}}, ...]}}\n\n'
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


def build_entity_list_prompt(
    chunk_text: str, corpus_summary: str = ""
) -> tuple[str, str]:
    return ENTITY_LIST_SYSTEM_PROMPT, ENTITY_LIST_USER_PROMPT_TEMPLATE.format(
        genre_not_topic=GENRE_NOT_TOPIC_PROMPT,
        corpus_summary=corpus_summary or "(vuoto)",
        entity_kernel_list=_ENTITY_KERNEL_LINES,
        chunk_text=chunk_text,
    )


def build_pair_relation_prompt(
    chunk_text: str,
    name_a: str,
    summary_a: str,
    name_b: str,
    summary_b: str,
    corpus_summary: str = "",
) -> tuple[str, str]:
    return PAIR_RELATION_SYSTEM_PROMPT, PAIR_RELATION_USER_PROMPT_TEMPLATE.format(
        genre_not_topic=GENRE_NOT_TOPIC_PROMPT,
        corpus_summary=corpus_summary or "(vuoto)",
        name_a=name_a,
        summary_a=summary_a,
        name_b=name_b,
        summary_b=summary_b,
        relation_kernel_list=_RELATION_KERNEL_LINES,
        chunk_text=chunk_text,
    )


def build_corpus_summary_prompt(
    existing_summary: str, document_text: str
) -> tuple[str, str]:
    return CORPUS_SUMMARY_SYSTEM_PROMPT, CORPUS_SUMMARY_USER_PROMPT_TEMPLATE.format(
        existing_summary=existing_summary,
        document_text=document_text,
    )


def build_event_entity_prompt(chunk_text: str) -> tuple[str, str]:
    return EVENT_ENTITY_SYSTEM_PROMPT, EVENT_ENTITY_USER_PROMPT_TEMPLATE.format(
        chunk_text=chunk_text
    )


def build_event_relation_prompt(chunk_text: str) -> tuple[str, str]:
    return EVENT_RELATION_SYSTEM_PROMPT, EVENT_RELATION_USER_PROMPT_TEMPLATE.format(
        relation_kernel_list=_RELATION_KERNEL_LINES,
        chunk_text=chunk_text,
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
