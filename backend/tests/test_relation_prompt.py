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


def test_system_prompt_matches_spec():
    expected = (
        "Confronta il FATTO NUOVO con il FATTO ESISTENTE e classifica la relazione tra i due:\n"
        '- `"replaces"` se il fatto nuovo contraddice o sostituisce il fatto esistente (es. cambia un '
        "valore, un'informazione più recente annulla o rimpiazza la precedente sullo stesso "
        "soggetto/attributo).\n"
        '- `"extends"` se il fatto nuovo aggiunge dettagli complementari, senza contraddire il fatto '
        "esistente: entrambi possono restare veri contemporaneamente.\n"
        '- `"none"` se non c\'è relazione significativa tra i due.\n\n'
        "Rispondi solo secondo lo schema fornito, senza aggiungere testo libero."
    )
    system, _user = build_relation_prompt("new", "old")
    assert system == expected
    assert SYSTEM_PROMPT == expected


def test_relation_edge_event_type_mapping():
    assert relation_edge_event_type(RelationLabel.replaces) == "updates"
    assert relation_edge_event_type(RelationLabel.extends) == "extends"
    assert relation_edge_event_type(RelationLabel.none) is None
