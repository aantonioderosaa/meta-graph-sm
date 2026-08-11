"""Consolidation prompts and LLM calls (tech-spec §6.2, E4.2)."""

from __future__ import annotations

from app.core.llm_client import call_structured
from app.models.consolidation import ConsolidationResult

SYSTEM_PROMPT = (
    "Sei un motore di consolidamento di fatti. Ricevi un gruppo di fatti semanticamente vicini "
    "estratti da una knowledge base. Se i fatti descrivono ripetizioni o frammenti di uno stesso "
    "pattern più generale, produci un'**astrazione** di livello più alto che sintetizza il pattern "
    '(`outcome="abstraction"`), elencando gli id di *tutti* i fatti sorgente usati. Se invece un '
    "fatto del gruppo è semplicemente una versione più chiara/pulita di un altro, senza costituire "
    "un pattern nuovo, produci la versione più pulita di quell'**unico** fatto "
    '(`outcome="cleaned_fact"`), lasciando `source_fact_ids` vuoto. Non inventare informazioni non '
    "presenti nei fatti forniti. Non fondere fatti che si contraddicono in un'unica affermazione: "
    "se noti una contraddizione, preferisci `cleaned_fact` sul fatto più recente/specifico e "
    "lascia "
    "che sia il passo successivo (classificazione relazioni) a gestirla."
)


def build_consolidation_prompt(facts: list[tuple[str, str]]) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for a fact group."""
    lines = [f"- [{fact_id}] {text}" for fact_id, text in facts]
    user_prompt = (
        "Fatti del gruppo:\n"
        + "\n".join(lines)
        + "\n\nProduci il consolidamento secondo lo schema fornito."
    )
    return SYSTEM_PROMPT, user_prompt


async def consolidate_group(
    facts: list[tuple[str, str]],
    job_id: str | None = None,
) -> ConsolidationResult:
    """Consolidate a group of semantically similar facts via structured LLM output."""
    system_prompt, user_prompt = build_consolidation_prompt(facts)
    return await call_structured(
        system_prompt,
        user_prompt,
        ConsolidationResult,
        temperature=0,
        job_id=job_id,
    )
