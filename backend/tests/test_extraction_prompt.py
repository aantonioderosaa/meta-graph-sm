"""Extraction prompt builder tests (E3.4)."""

from __future__ import annotations

from app.pipeline.extraction import SYSTEM_PROMPT, build_extraction_prompt


def test_user_prompt_wraps_chunk_in_triple_quotes():
    chunk_text = "Alice works at Acme Corp."
    _system, user = build_extraction_prompt(chunk_text)
    assert f'"""{chunk_text}"""' in user
    assert user.startswith("Testo:")
    assert "Estrai i fatti atomici secondo lo schema fornito." in user


def test_system_prompt_matches_spec():
    expected = (
        "Sei un estrattore di fatti atomici da testo. Estrai solo affermazioni autosufficienti "
        "(comprensibili senza il contesto del chunk), verificabili o comunque dichiarative. "
        'Ignora saluti, conferme vuote ("ok", "capito"), domande retoriche, '
        "filler conversazionale. "
        "Classifica ogni fatto come `fact` (affermazione oggettiva/duratura), `preference` "
        "(gusto o scelta soggettiva dell'utente) oppure `episode` (evento specifico, puntuale, "
        "spesso datato). Se il testo non contiene alcun fatto utile, restituisci una lista vuota. "
        "Non inventare informazioni non presenti nel testo."
    )
    system, _user = build_extraction_prompt("fixture")
    assert system == expected
    assert SYSTEM_PROMPT == expected
