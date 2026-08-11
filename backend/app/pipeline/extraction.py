"""Fact extraction prompts and LLM calls (tech-spec §5.3, E3.4)."""

from __future__ import annotations

from app.core.llm_client import call_structured
from app.models.extraction import FactExtractionResult

SYSTEM_PROMPT = (
    "Sei un estrattore di fatti atomici da testo. Estrai solo affermazioni autosufficienti "
    "(comprensibili senza il contesto del chunk), verificabili o comunque dichiarative. "
    'Ignora saluti, conferme vuote ("ok", "capito"), domande retoriche, filler conversazionale. '
    "Classifica ogni fatto come `fact` (affermazione oggettiva/duratura), `preference` "
    "(gusto o scelta soggettiva dell'utente) oppure `episode` (evento specifico, puntuale, "
    "spesso datato). Se il testo non contiene alcun fatto utile, restituisci una lista vuota. "
    "Non inventare informazioni non presenti nel testo."
)


def build_extraction_prompt(chunk_text: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) with chunk_text substituted."""
    user_prompt = (
        "Testo:\n"
        f'"""{chunk_text}"""\n\n'
        "Estrai i fatti atomici secondo lo schema fornito."
    )
    return SYSTEM_PROMPT, user_prompt


async def extract_facts(chunk_text: str, job_id: str | None = None) -> FactExtractionResult:
    """Extract atomic facts from chunk text via structured LLM output."""
    system_prompt, user_prompt = build_extraction_prompt(chunk_text)
    result = await call_structured(
        system_prompt,
        user_prompt,
        FactExtractionResult,
        temperature=0,
        job_id=job_id,
    )
    return result
