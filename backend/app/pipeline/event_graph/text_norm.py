"""Unicode and referential-form normalization (piano Parte B).

Importable without cycles: used by ``ids.py`` and ``mention_coref.py``.
"""

from __future__ import annotations

import unicodedata

# Curly apostrophes / quotes → ASCII straight equivalents.
_CURLY_TO_STRAIGHT = str.maketrans(
    {
        "\u2018": "'",  # ‘
        "\u2019": "'",  # ’
        "\u201a": "'",  # ‚
        "\u201b": "'",  # ‛
        "\u2032": "'",  # ′
        "\u00b4": "'",  # ´
        "\u02bc": "'",  # ʼ
        "\u201c": '"',  # “
        "\u201d": '"',  # ”
        "\u201e": '"',  # „
        "\u201f": '"',  # ‟
        "\u00ab": '"',  # «
        "\u00bb": '"',  # »
    }
)

# Longest first so "uno" wins over "un", "an" over "a", "gli"/"il" over "i",
# and "della"/"allo" win over "del"/"al".
_DETERMINERS: tuple[str, ...] = tuple(
    sorted(
        (
            "il",
            "lo",
            "la",
            "i",
            "gli",
            "le",
            "l'",
            "un",
            "uno",
            "una",
            "the",
            "a",
            "an",
            "allo",
            "alla",
            "all'",
            "al",
            "dello",
            "della",
            "dell'",
            "del",
            "dallo",
            "dalla",
            "dall'",
            "dal",
            "nello",
            "nella",
            "nell'",
            "nel",
            "sullo",
            "sulla",
            "sull'",
            "sul",
            "col",
        ),
        key=len,
        reverse=True,
    )
)
_DISCOURSE_ADVERBS = frozenset(
    {
        "invece",
        "però",
        "pero",
        "quindi",
        "allora",
        "inoltre",
        "instead",
        "however",
        "meanwhile",
        "though",
    }
)


def fold_text(text: str) -> str:
    """NFC + curly apostrophe/quote fold. Does not strip determiners."""
    if not text:
        return ""
    return unicodedata.normalize("NFC", text).translate(_CURLY_TO_STRAIGHT)


def _strip_one_determiner(text: str) -> str:
    for det in _DETERMINERS:
        if not text.startswith(det):
            continue
        rest = text[len(det) :]
        if det.endswith("'") or not rest or rest[0].isspace():
            return rest.lstrip()
    return text


def _normalize_referential(forma: str) -> str:
    """Identity key: cleaned grammatical head (no articles, adjectives, adverbs)."""
    from app.pipeline.event_graph.entita_forma import forma_identita

    chiave = forma_identita(forma)
    if chiave:
        return chiave
    folded = fold_text(forma or "").casefold()
    for mark in (",", ";", ":", "!", "?"):
        folded = folded.replace(mark, " ")
    stripped = _strip_one_determiner(folded)
    tokens = [
        tok for tok in stripped.split() if tok not in _DISCOURSE_ADVERBS
    ]
    return " ".join(tokens)


__all__ = ["fold_text", "_normalize_referential"]
