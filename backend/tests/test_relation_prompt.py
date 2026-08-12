"""Relation classification prompt builder tests (E4.4)."""

# ruff: noqa: E501

from __future__ import annotations

from app.models.relations import RelationLabel
from app.pipeline.relations import SYSTEM_PROMPT, build_relation_prompt, relation_edge_event_type


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


def test_system_prompt_extends_rewritten_replaces_unchanged():
    """R2.2: extends broadened with examples; replaces stays byte-identical."""
    from app.pipeline.relations import _REPLACES_SECTION

    assert SYSTEM_PROMPT.startswith(
        "Confronta il FATTO NUOVO con il FATTO ESISTENTE e classifica la relazione tra i due:\n"
    )
    assert _REPLACES_SECTION in SYSTEM_PROMPT
    # Exact historical replaces wording (pre-R2.2), including the trailing newline.
    assert (
        '- `"replaces"` se il fatto nuovo contraddice o sostituisce il fatto esistente (es. cambia un '
        "valore, un'informazione più recente annulla o rimpiazza la precedente sullo stesso "
        "soggetto/attributo).\n"
    ) == _REPLACES_SECTION
    assert "stessa situazione/episodio complessivo" in SYSTEM_PROMPT
    assert "Esempio extends:" in SYSTEM_PROMPT
    assert "Esempio none:" in SYSTEM_PROMPT
    assert "momenti diversi dello stesso episodio narrativo" in SYSTEM_PROMPT
    assert "argomenti scorrelati" in SYSTEM_PROMPT


def test_relation_edge_event_type_mapping():
    assert relation_edge_event_type(RelationLabel.replaces) == "updates"
    assert relation_edge_event_type(RelationLabel.extends) == "extends"
    assert relation_edge_event_type(RelationLabel.none) is None
