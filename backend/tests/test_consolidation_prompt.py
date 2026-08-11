"""Consolidation prompt builder tests (E4.2)."""

# ruff: noqa: E501

from __future__ import annotations

from app.pipeline.consolidation import SYSTEM_PROMPT, build_consolidation_prompt


def test_user_prompt_lists_all_facts_in_order():
    facts = [
        ("id-1", "Alice works at Acme."),
        ("id-2", "Alice is employed by Acme Corp."),
        ("id-3", "Acme employs Alice."),
    ]
    _system, user = build_consolidation_prompt(facts)
    assert "- [id-1] Alice works at Acme." in user
    assert "- [id-2] Alice is employed by Acme Corp." in user
    assert "- [id-3] Acme employs Alice." in user
    assert user.index("- [id-1]") < user.index("- [id-2]") < user.index("- [id-3]")
    assert user.startswith("Fatti del gruppo:")
    assert "Produci il consolidamento secondo lo schema fornito." in user


def test_system_prompt_matches_spec():
    expected = (
        "Sei un motore di consolidamento di fatti. Ricevi un gruppo di fatti semanticamente vicini "
        "estratti da una knowledge base. Se i fatti descrivono ripetizioni o frammenti di uno stesso "
        "pattern più generale, produci un'**astrazione** di livello più alto che sintetizza il pattern "
        '(`outcome="abstraction"`), elencando gli id di *tutti* i fatti sorgente usati. Se invece un '
        "fatto del gruppo è semplicemente una versione più chiara/pulita di un altro, senza costituire "
        "un pattern nuovo, produci la versione più pulita di quell'**unico** fatto "
        '(`outcome="cleaned_fact"`), lasciando `source_fact_ids` vuoto. Non inventare informazioni non '
        "presenti nei fatti forniti. Non fondere fatti che si contraddicono in un'unica affermazione: "
        "se noti una contraddizione, preferisci `cleaned_fact` sul fatto più recente/specifico e lascia "
        "che sia il passo successivo (classificazione relazioni) a gestirla."
    )
    system, _user = build_consolidation_prompt([("x", "y")])
    assert system == expected
    assert SYSTEM_PROMPT == expected
