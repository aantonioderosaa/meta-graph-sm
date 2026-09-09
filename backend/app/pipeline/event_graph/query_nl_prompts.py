"""Thin bilingual prompts for NL → EventQuerySpec (piano sez. 15.4 / M18)."""

from __future__ import annotations

SYSTEM_NL_QUERY = """You compile a free-text question into an EventQuerySpec.
Return only the structured EventQuerySpec fields. Do not write Cypher.
Do not invent filters the text does not support. Unused fields stay null.

Closed vocabularies (pick exactly one value or null):
- piano ∈ {PRIMO_PIANO, SFONDO, FUORI_LINEA}
- fattualita ∈ {FATTUALE, NON_FATTUALE, IPOTETICO}
- tempo ∈ {presente, imperfetto, passato, trapassato, futuro, non_finito}
- tipo_relazione ∈ {SOGG, OGG, OBL, TEMPO, LUOGO, MODO, CAUSA, PRECEDE,
  LIMITE, CONDIZIONE, SCOPO, CONCESSIONE, CONTRASTO, SEQUENZA, CONTENUTO,
  COLLEGATO, SATELLITE_DI}
- traversal ∈ {catena_di, spina_dorsale_di, prima_di, dopo_di, vicinato_temporale}
  If traversal is set, traversal_target MUST be the event id mentioned in the text.
  If no event id is given, leave both traversal and traversal_target null.
- finestra_tempo_assoluto: optional {da, a} as ISO YYYY / YYYY-MM / YYYY-MM-DD / datetime.
- lemma, fonte, documento: surface strings only when the question names them.

English and Italian. Temperature is 0."""


def user_nl_query(testo: str) -> str:
    return f"Compile this question into EventQuerySpec:\n\n{testo}\n"


__all__ = ["SYSTEM_NL_QUERY", "user_nl_query"]
