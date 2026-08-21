"""F24.3: marker word-boundary matching. No Docker/OpenAI."""

from __future__ import annotations

import pytest

from app.models.relations import RelationLabel
from app.pipeline.context_layer_eval import all_t2_markers, collision_control_sentences
from app.pipeline.entity_relation_resolution import (
    contains_lexical_marker,
    map_temporal_transition,
)
from app.pipeline.relevance_gate import classify_fragment_relevance, match_t2_marker


def test_finora_is_not_succession_ora_e_still_is():
    """Dedicated regression: «finora è» must not fire «ora è»."""
    assert contains_lexical_marker("ora è presidente", "ora è") is True
    assert contains_lexical_marker("Da allora ora è presidente.", "ora è") is True
    assert contains_lexical_marker("Finora è rimasto in cucina.", "ora è") is False
    assert contains_lexical_marker("finora è tutto tranquillo", "ora è") is False

    finora = match_t2_marker("Finora è rimasto in cucina senza cambiare ruolo.")
    assert finora is None or finora[1] != "ora è"

    genuine = classify_fragment_relevance("Da allora ora è presidente.", [])
    assert genuine is not None
    assert genuine.kind == "t2"
    assert genuine.marker_category == "succession"
    suc = match_t2_marker("Da allora ora è presidente.")
    assert suc is not None and suc[0] == "succession"
    assert contains_lexical_marker("Da allora ora è presidente.", "ora è") is True


def test_finora_is_not_supersedes_in_temporal_map():
    label = map_temporal_transition(
        "Finora è rimasto in cucina.",
        "Il gatto dorme sul divano.",
    )
    assert label != RelationLabel.supersedes


def test_real_word_collisions_do_not_fire_wrapped_marker():
    cases = [
        ("ora è", "Finora è rimasto fermo, senza successione di ruolo."),
        ("ogni ", "I sogni della bambina sono sereni e non riguardano un quantificatore."),
        ("all the ", "They call the office about an ordinary invoice."),
        ("presidente", "Il vicepresidente ha firmato un verbale di routine."),
        ("tutti i ", "Nell'intutti i registri restano chiusi in archivio."),
    ]
    for marker, sentence in cases:
        hit = match_t2_marker(sentence)
        assert hit is None or hit[1] != marker, (marker, sentence, hit)


@pytest.mark.parametrize("category,marker", all_t2_markers())
def test_generative_collision_controls_do_not_fire_via_substring(category, marker):
    """For every T2 marker, prefixing its first token must not still match it."""
    _ = category
    for sentence in collision_control_sentences(marker):
        hit = match_t2_marker(sentence)
        if hit is None:
            continue
        assert hit[1] != marker, (marker, sentence, hit)


def test_prefix_stems_and_genuine_markers_still_match():
    assert match_t2_marker("Tutti i cani sono usciti.")[0] == "quantifier"
    assert match_t2_marker("Ogni studente ha lasciato l'aula.")[0] == "quantifier"
    assert match_t2_marker("Ciascuno dei cani è uscito.")[0] == "quantifier"
    assert match_t2_marker("Dal 2018 Weah è presidente.")[0] == "succession"
    assert match_t2_marker("In realtà mi sono sbagliato sul datore.")[0] == "error"
    assert match_t2_marker("Tutto quello che ti ho detto finora è falso.")[0] == "retraction"
    suc = match_t2_marker("Da allora ora è presidente.")
    assert suc is not None and suc[0] == "succession"
