"""F24.1 labeled eval corpus for the agentic context layer.

Frozen Italian (and a few English) narrative fragments. No live LLM at import
time. Four classes:

* (a) known lexical markers — true positives for T2
* (b) substring-collision controls — currently false fires before F24.3
* (c) paraphrases without a lexical marker — true positives T1/T2/T3 miss
* (d) ordinary unrelated facts — true negatives
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EvalClass = Literal["a", "b", "c", "d"]
EvalCategory = Literal["quantifier", "retraction", "error", "succession"]


@dataclass(frozen=True)
class EvalItem:
    id: str
    text: str
    expected_signal: bool
    expected_category: EvalCategory | None
    class_: EvalClass
    note: str
    usable_pair: bool = True
    relation_written: bool = True


CONTEXT_LAYER_EVAL_CORPUS: tuple[EvalItem, ...] = (
    # --- (a) known markers, true positives ---
    EvalItem(
        id="a-quant-cani",
        text="Tutti i cani sono usciti.",
        expected_signal=True,
        expected_category="quantifier",
        class_="a",
        note="Canonical T2 quantifier marker «tutti i».",
    ),
    EvalItem(
        id="a-quant-ogni",
        text="Ogni studente ha lasciato l'aula.",
        expected_signal=True,
        expected_category="quantifier",
        class_="a",
        note="T2 quantifier marker «ogni».",
    ),
    EvalItem(
        id="a-retr-detto",
        text="Tutto quello che ti ho detto finora è falso.",
        expected_signal=True,
        expected_category="retraction",
        class_="a",
        note="Canonical T2 retraction; «finora è» must not steal succession.",
    ),
    EvalItem(
        id="a-retr-niente",
        text="Non è vero niente di quella storia.",
        expected_signal=True,
        expected_category="retraction",
        class_="a",
        note="T2 retraction marker «non è vero niente».",
    ),
    EvalItem(
        id="a-err-sbagliato",
        text="In realtà mi sono sbagliato sul datore.",
        expected_signal=True,
        expected_category="error",
        class_="a",
        note="Canonical T2 error marker «mi sono sbagliato».",
    ),
    EvalItem(
        id="a-err-errore",
        text="Era un errore: Alice non lavora ad Acme.",
        expected_signal=True,
        expected_category="error",
        class_="a",
        note="T2 error marker «era un errore».",
    ),
    EvalItem(
        id="a-succ-ora",
        text="Da allora ora è presidente.",
        expected_signal=True,
        expected_category="succession",
        class_="a",
        note="Genuine succession: «ora è» as a whole marker, not inside «finora».",
    ),
    EvalItem(
        id="a-succ-da-allora",
        text="Da allora è presidente.",
        expected_signal=True,
        expected_category="succession",
        class_="a",
        note="T2 succession via «da allora» / «presidente».",
    ),
    # --- (b) substring collisions — must not fire the wrapped marker ---
    EvalItem(
        id="b-finora-cucina",
        text="Finora è rimasto in cucina senza cambiare ruolo.",
        expected_signal=False,
        expected_category=None,
        class_="b",
        note="«finora è» contains substring «ora è»; not a succession.",
    ),
    EvalItem(
        id="b-finora-tranquillo",
        text="Finora è tutto tranquillo in ufficio, nessun cambio di stato.",
        expected_signal=False,
        expected_category=None,
        class_="b",
        note="Same «ora è»-inside-«finora è» collision, different sentence.",
    ),
    EvalItem(
        id="b-sogni",
        text="I sogni della bambina sono sereni stanotte.",
        expected_signal=False,
        expected_category=None,
        class_="b",
        note="«sogni » contains substring «ogni » of the quantifier list.",
    ),
    EvalItem(
        id="b-vicepresidente",
        text="Il vicepresidente ha firmato un verbale di routine.",
        expected_signal=False,
        expected_category=None,
        class_="b",
        note="«vicepresidente» contains substring «presidente».",
    ),
    EvalItem(
        id="b-call-the",
        text="They call the office about an ordinary invoice.",
        expected_signal=False,
        expected_category=None,
        class_="b",
        note="«call the » contains substring «all the » of the quantifier list.",
    ),
    EvalItem(
        id="b-intutti",
        text="Nell'intutti i registri restano chiusi in archivio.",
        expected_signal=False,
        expected_category=None,
        class_="b",
        note="«intutti i » contains substring «tutti i ».",
    ),
    # --- (c) paraphrases without a lexical T2 marker ---
    EvalItem(
        id="c-err-non-piu-da",
        text="Alice lavora da Beta, non più da Acme.",
        expected_signal=True,
        expected_category="error",
        class_="c",
        note="Paraphrased correction; no «mi sono sbagliato».",
    ),
    EvalItem(
        id="c-err-ruolo",
        text="Mario non è più consulente: il suo ruolo attuale è direttore.",
        expected_signal=True,
        expected_category="error",
        class_="c",
        note="Role correction without an error-list marker.",
    ),
    EvalItem(
        id="c-retr-ritiro",
        text="Quanto detto prima non vale più: ritiro quelle affermazioni.",
        expected_signal=True,
        expected_category="retraction",
        class_="c",
        note="Retraction paraphrase without «tutto è falso» / «ti ho detto».",
    ),
    EvalItem(
        id="c-quant-nessuno",
        text="Rex, Fido e Luna sono usciti, nessuno è rimasto in casa.",
        expected_signal=True,
        expected_category="quantifier",
        class_="c",
        note="Collective scene without «tutti i» / «ogni».",
    ),
    EvalItem(
        id="c-quant-gruppo",
        text="L'intero gruppo ha lasciato la cucina.",
        expected_signal=True,
        expected_category="quantifier",
        class_="c",
        note="Quantifier paraphrase «l'intero gruppo».",
    ),
    EvalItem(
        id="c-succ-forza",
        text="Il precedente datore era Acme; adesso Alice è in forza a Beta.",
        expected_signal=True,
        expected_category="succession",
        class_="c",
        note="Employment succession without «ora è» / «da allora».",
    ),
    # --- (d) ordinary unrelated facts — true negatives ---
    EvalItem(
        id="d-alice-acme",
        text="Alice lavora ad Acme come analista.",
        expected_signal=False,
        expected_category=None,
        class_="d",
        note="Simply new fact; must not grow a structural signal.",
    ),
    EvalItem(
        id="d-ufficio",
        text="L'ufficio è a Milano e ha una palestra sul tetto.",
        expected_signal=False,
        expected_category=None,
        class_="d",
        note="Complementary details, not a succession or correction.",
    ),
    EvalItem(
        id="d-vento",
        text="Il vento soffiava forte sulla strada.",
        expected_signal=False,
        expected_category=None,
        class_="d",
        note="Narrative weather; no context-layer signal.",
    ),
    EvalItem(
        id="d-fido-cibo",
        text="Fido ha mangiato le crocchette in cucina.",
        expected_signal=False,
        expected_category=None,
        class_="d",
        note="Ordinary named-entity fact with a usable pair.",
    ),
    EvalItem(
        id="d-invoice",
        text="The invoice was paid on Tuesday.",
        expected_signal=False,
        expected_category=None,
        class_="d",
        note="English ordinary fact; no quantifier/retraction/error/succession.",
    ),
    EvalItem(
        id="d-riunione",
        text="La riunione inizia alle nove in sala B.",
        expected_signal=False,
        expected_category=None,
        class_="d",
        note="Scheduling fact, not a structural context signal.",
    ),
)


def test_corpus_covers_four_classes_and_is_frozen():
    ids = [item.id for item in CONTEXT_LAYER_EVAL_CORPUS]
    assert len(ids) == len(set(ids))
    assert 16 <= len(CONTEXT_LAYER_EVAL_CORPUS) <= 32
    by_class = {cls: 0 for cls in ("a", "b", "c", "d")}
    for item in CONTEXT_LAYER_EVAL_CORPUS:
        assert isinstance(item.text, str) and item.text
        assert item.class_ in by_class
        by_class[item.class_] += 1
        if item.class_ in {"a", "c"}:
            assert item.expected_signal is True
        else:
            assert item.expected_signal is False
            assert item.expected_category is None
        if item.class_ == "a":
            assert item.expected_category in {
                "quantifier",
                "retraction",
                "error",
                "succession",
            }
    assert all(count >= 4 for count in by_class.values()), by_class
    assert any("finora è" in item.text.casefold() for item in CONTEXT_LAYER_EVAL_CORPUS)
    assert any(item.id == "c-err-non-piu-da" for item in CONTEXT_LAYER_EVAL_CORPUS)
