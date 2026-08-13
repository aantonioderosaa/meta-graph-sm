"""Relation classification prompt builder tests (E4.4)."""

# ruff: noqa: E501

from __future__ import annotations

from app.pipeline.entity_relation_resolution import (
    _REPLACES_SECTION,
    SYSTEM_PROMPT,
    build_relation_prompt,
)


def test_user_prompt_substitutes_fact_texts():
    n_text = "Alice now works at Beta Corp."
    v_text = "Alice works at Acme Corp."
    _system, user = build_relation_prompt(n_text, v_text)
    assert f'FATTO NUOVO: "{n_text}"' in user
    assert f'FATTO ESISTENTE: "{v_text}"' in user
    assert "Classifica la relazione." in user


def test_user_prompt_without_locality_matches_baseline():
    """R2.1: no flags → user prompt textually identical to pre-locality baseline."""
    expected = (
        'FATTO NUOVO: "new"\n'
        'FATTO ESISTENTE: "old"\n'
        "\nClassifica la relazione."
    )
    _system, user = build_relation_prompt("new", "old")
    assert user == expected
    assert "Nota:" not in user


def test_user_prompt_same_chunk_note():
    _system, user = build_relation_prompt("n", "v", same_chunk=True)
    assert "Nota: i due fatti provengono dallo stesso passaggio di testo." in user
    assert "stesso documento" not in user


def test_user_prompt_same_doc_note_only_when_not_same_chunk():
    _system, user = build_relation_prompt("n", "v", same_doc=True)
    assert "Nota: i due fatti provengono dallo stesso documento." in user
    assert "passaggio di testo" not in user

    _system, both = build_relation_prompt("n", "v", same_chunk=True, same_doc=True)
    assert "passaggio di testo" in both
    assert "stesso documento" not in both


def test_system_prompt_matches_spec():
    system, _user = build_relation_prompt("new", "old")
    assert system == SYSTEM_PROMPT


def test_system_prompt_extends_section_present():
    """R2.2: extends broadened with examples; still present after T1 replaces rewrite."""
    assert SYSTEM_PROMPT.startswith(
        "Confronta il FATTO NUOVO con il FATTO ESISTENTE e classifica la relazione tra i due:\n"
    )
    assert "stessa situazione/episodio complessivo" in SYSTEM_PROMPT
    assert "Esempio extends:" in SYSTEM_PROMPT
    assert "Esempio none:" in SYSTEM_PROMPT
    assert "momenti diversi dello stesso episodio narrativo" in SYSTEM_PROMPT
    assert "argomenti scorrelati" in SYSTEM_PROMPT


def test_system_prompt_replaces_temporal_markers_and_label_disambiguation():
    """T1.1: replaces prioritizes temporal markers; labels are not chronological."""
    assert _REPLACES_SECTION in SYSTEM_PROMPT
    assert "cerca marcatori temporali" in _REPLACES_SECTION
    assert "date assolute" in _REPLACES_SECTION
    assert '"ora"' in _REPLACES_SECTION
    assert '"da allora"' in _REPLACES_SECTION
    assert '"fino al"' in _REPLACES_SECTION
    assert '"ho appena iniziato"' in _REPLACES_SECTION
    assert '"il mese scorso"' in _REPLACES_SECTION
    assert "base primaria" in _REPLACES_SECTION
    assert (
        "Le etichette FATTO NUOVO/FATTO ESISTENTE indicano solo quale dei due stai "
        "valutando ora — non implicano da sole che uno sia temporalmente precedente "
        "all'altro."
    ) in _REPLACES_SECTION


def test_build_relation_prompt_still_substitutes_placeholders():
    """T1.1: builder still substitutes fact texts; system prompt is the shared constant."""
    n_text = "Dal 2024 Alice lavora in Beta."
    v_text = "Fino al 2023 Alice lavorava in Acme."
    system, user = build_relation_prompt(n_text, v_text)
    assert system == SYSTEM_PROMPT
    assert f'FATTO NUOVO: "{n_text}"' in user
    assert f'FATTO ESISTENTE: "{v_text}"' in user
    assert "Classifica la relazione." in user
    assert "cerca marcatori temporali" in system


def test_system_prompt_prudence_rule_when_no_temporal_marker():
    """T1.2: without an explicit temporal marker, do not force replaces; risk asymmetry stated."""
    assert (
        "Se nessuno dei due fatti contiene un marcatore temporale esplicito che stabilisca "
        "quale dei due descrive lo stato più recente, non scegliere `replaces` sulla sola "
        "base dell'ordine di presentazione"
    ) in SYSTEM_PROMPT
    assert "valuta invece se i due fatti possono coesistere (`extends`)" in SYSTEM_PROMPT
    assert "non c'è relazione significativa (`none`)" in SYSTEM_PROMPT
    assert (
        "Dichiarare erroneamente `replaces` nasconde un fatto vero: è un errore peggiore "
        "di non dichiarare nulla."
    ) in SYSTEM_PROMPT


def test_system_prompt_t1_blocks_present_for_ci():
    """T1.4: both new prompt blocks (temporal markers + prudence) stay in SYSTEM_PROMPT."""
    assert "cerca marcatori temporali" in SYSTEM_PROMPT
    assert "base primaria della decisione" in SYSTEM_PROMPT
    assert "Le etichette FATTO NUOVO/FATTO ESISTENTE indicano solo" in SYSTEM_PROMPT
    assert "marcatore temporale esplicito" in SYSTEM_PROMPT
    assert "errore peggiore di non dichiarare nulla" in SYSTEM_PROMPT
