"""Relation classification prompt builder tests (E4.4 / Fase 9)."""

# ruff: noqa: E501

from __future__ import annotations

from app.pipeline.entity_relation_resolution import (
    _REPLACES_SECTION,
    _TEMPORAL_TRANSITIONS_SECTION,
    LEGACY_SYSTEM_PROMPT,
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
    """R2.2: extends broadened with examples; still present after T1/F9 rewrite."""
    assert SYSTEM_PROMPT.startswith(
        "Confronta il FATTO NUOVO con il FATTO ESISTENTE e classifica la relazione tra i due:\n"
    )
    assert "stessa situazione/episodio complessivo" in SYSTEM_PROMPT
    assert "Esempio extends:" in SYSTEM_PROMPT
    assert "Esempio none:" in SYSTEM_PROMPT
    assert "momenti diversi dello stesso episodio narrativo" in SYSTEM_PROMPT
    assert "argomenti scorrelati" in SYSTEM_PROMPT
    assert "extends non è una transizione di versione" in SYSTEM_PROMPT


def test_system_prompt_three_way_temporal_section():
    """F9.3: supersedes / updated_by / contradicts; temporal markers stay the primary cue."""
    assert _TEMPORAL_TRANSITIONS_SECTION in SYSTEM_PROMPT
    assert '"supersedes"' in _TEMPORAL_TRANSITIONS_SECTION
    assert '"updated_by"' in _TEMPORAL_TRANSITIONS_SECTION
    assert '"contradicts"' in _TEMPORAL_TRANSITIONS_SECTION
    assert "cerca marcatori temporali" in _TEMPORAL_TRANSITIONS_SECTION
    assert "date assolute" in _TEMPORAL_TRANSITIONS_SECTION
    assert '"ora"' in _TEMPORAL_TRANSITIONS_SECTION
    assert '"da allora"' in _TEMPORAL_TRANSITIONS_SECTION
    assert '"fino al"' in _TEMPORAL_TRANSITIONS_SECTION
    assert '"dal 2018"' in _TEMPORAL_TRANSITIONS_SECTION
    assert '"ho appena iniziato"' in _TEMPORAL_TRANSITIONS_SECTION
    assert '"il mese scorso"' in _TEMPORAL_TRANSITIONS_SECTION
    assert "base primaria" in _TEMPORAL_TRANSITIONS_SECTION
    assert (
        "Le etichette FATTO NUOVO/FATTO ESISTENTE indicano solo quale dei due stai "
        "valutando ora — non implicano da sole che uno sia temporalmente precedente "
        "all'altro."
    ) in _TEMPORAL_TRANSITIONS_SECTION


def test_system_prompt_updated_by_requires_explicit_error():
    """F9.5: UPDATED_BY only if explicit error/correction wording."""
    assert "in realtà mi sono sbagliato" in SYSTEM_PROMPT
    assert "Senza quel marcatore di errore nel testo, non scegliere `updated_by`" in SYSTEM_PROMPT
    assert "mai `updated_by`" in SYSTEM_PROMPT
    assert (
        "due fonti autorevoli in conflitto senza marcatore di errore → `contradicts`, "
        "mai `updated_by`"
    ) in SYSTEM_PROMPT


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
    """T1.2 / F9: without an explicit temporal marker, do not force supersedes/replaces."""
    assert (
        "Se nessuno dei due fatti contiene un marcatore temporale esplicito che stabilisca "
        "quale dei due descrive lo stato più recente, non scegliere `supersedes` (né "
        "`replaces`) sulla sola base dell'ordine di presentazione"
    ) in SYSTEM_PROMPT
    assert "valuta invece se i due fatti possono coesistere (`extends`)" in SYSTEM_PROMPT
    assert "disaccordo senza rettifica (`contradicts`)" in SYSTEM_PROMPT
    assert "non c'è relazione significativa (`none`)" in SYSTEM_PROMPT
    assert (
        "Dichiarare erroneamente una transizione che nasconde un fatto vero è un errore "
        "peggiore di non dichiarare nulla."
    ) in SYSTEM_PROMPT


def test_system_prompt_t1_blocks_present_for_ci():
    """T1.4: temporal markers + prudence stay in the default (three-way) SYSTEM_PROMPT."""
    assert "cerca marcatori temporali" in SYSTEM_PROMPT
    assert "base primaria della decisione" in SYSTEM_PROMPT
    assert "Le etichette FATTO NUOVO/FATTO ESISTENTE indicano solo" in SYSTEM_PROMPT
    assert "marcatore temporale esplicito" in SYSTEM_PROMPT
    assert "errore peggiore di non dichiarare nulla" in SYSTEM_PROMPT


def test_legacy_prompt_kept_for_flag_off(monkeypatch):
    """Both flags False → T1 replaces/extends/none prompt (tests can target it)."""
    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.settings.ENABLE_TEMPORAL_TRANSITIONS",
        False,
    )
    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.settings.ENABLE_FACET_IDENTITY",
        False,
    )
    system, _user = build_relation_prompt("new", "old")
    assert system == LEGACY_SYSTEM_PROMPT
    assert _REPLACES_SECTION in system
    assert "`replaces`" in system
    assert _TEMPORAL_TRANSITIONS_SECTION not in system
