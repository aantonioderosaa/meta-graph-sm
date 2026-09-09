"""M0 — zone segmentation by lexical cohesion (TextTiling-style).

Sliding windows of sentences, Jaccard overlap of content tokens, Hearst
depth scores at similarity valleys. Conservative: prefer one zone to many.
Zero new dependencies (isolation D6).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.chunking_periods import (
    SentenceSpan,
    split_sentences_with_offsets,
)
from app.pipeline.event_graph.ids import zona_id

# Fewer sentences than this → a single zone (under-segmentation).
_MIN_SENTENCES = 6
# Hearst cutoff: depth > mean(depth) - k * std.
_DEPTH_K = 0.5
# If every gap similarity is at least this, do not split.
_HIGH_SIM = 0.25
# A valley must dip below this Jaccard to count as a topic boundary.
_VALLEY_MAX_SIM = 0.12
# Minimum sentences on each side of an accepted boundary.
_MIN_ZONE_SENTENCES = 3

_TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+")

# Tiny frozen IT+EN function-word set (not a dependency).
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "than",
        "so",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "by",
        "with",
        "from",
        "as",
        "into",
        "onto",
        "upon",
        "over",
        "under",
        "above",
        "below",
        "between",
        "among",
        "through",
        "across",
        "along",
        "around",
        "near",
        "beside",
        "beyond",
        "about",
        "against",
        "without",
        "within",
        "is",
        "am",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "will",
        "would",
        "can",
        "could",
        "may",
        "might",
        "shall",
        "should",
        "must",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "he",
        "she",
        "they",
        "we",
        "you",
        "i",
        "him",
        "her",
        "them",
        "his",
        "their",
        "our",
        "your",
        "my",
        "not",
        "no",
        "nor",
        "also",
        "just",
        "only",
        "very",
        "too",
        "more",
        "most",
        "some",
        "any",
        "each",
        "every",
        "all",
        "both",
        "other",
        "such",
        "there",
        "here",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "what",
        "how",
        "il",
        "lo",
        "la",
        "i",
        "gli",
        "le",
        "un",
        "uno",
        "una",
        "di",
        "a",
        "da",
        "in",
        "su",
        "per",
        "con",
        "tra",
        "fra",
        "e",
        "o",
        "ma",
        "se",
        "che",
        "chi",
        "cui",
        "è",
        "sono",
        "era",
        "erano",
        "fui",
        "fu",
        "ha",
        "ho",
        "hanno",
        "questo",
        "questa",
        "questi",
        "queste",
        "quello",
        "quella",
        "quelli",
        "quelle",
        "si",
        "non",
        "come",
        "più",
        "anche",
        "dei",
        "del",
        "dello",
        "della",
        "delle",
        "degli",
        "al",
        "allo",
        "alla",
        "ai",
        "agli",
        "alle",
        "dal",
        "dallo",
        "dalla",
        "dai",
        "dagli",
        "dalle",
        "nel",
        "nello",
        "nella",
        "nei",
        "negli",
        "nelle",
        "sul",
        "sullo",
        "sulla",
        "sui",
        "sugli",
        "sulle",
    }
)


@dataclass
class Zona:
    id: str
    documento: str
    offset_inizio: int
    offset_fine: int
    ordinale: int
    testo: str
    riassunto: str = ""
    entita_principali: list[str] = field(default_factory=list)
    ancore_temporali: list[str] = field(default_factory=list)
    evento_centrale: str | None = None
    espansa: bool = False
    regola: str = "M0_texttiling"
    versione_regole: str = RULESET_VERSION
    avviso: str | None = None
    su_via_principale: list[str] = field(default_factory=list)


def _content_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        tok = raw.lower()
        if tok in _STOPWORDS:
            continue
        tokens.add(tok)
    return tokens


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def _tile_spans(n_sentences: int, window: int) -> list[tuple[int, int]]:
    """Non-overlapping sentence tiles of ``window`` (last remainder absorbed)."""
    if n_sentences <= 0 or window < 1:
        return []
    tiles: list[tuple[int, int]] = []
    start = 0
    while start < n_sentences:
        end = min(start + window, n_sentences) - 1
        tiles.append((start, end))
        start = end + 1
    if len(tiles) >= 2:
        last_start, last_end = tiles[-1]
        if last_end - last_start + 1 < window:
            prev_start, _prev_end = tiles[-2]
            tiles[-2] = (prev_start, last_end)
            tiles.pop()
    return tiles


def _gap_similarities(
    sent_tokens: list[set[str]],
    tiles: list[tuple[int, int]],
    block_k: int,
) -> tuple[list[int], list[float]]:
    bags: list[set[str]] = []
    for start, end in tiles:
        bag: set[str] = set()
        for index in range(start, end + 1):
            bag |= sent_tokens[index]
        bags.append(bag)

    n_tiles = len(bags)
    gap_after: list[int] = []
    scores: list[float] = []
    for tile_gap in range(block_k - 1, n_tiles - block_k):
        left: set[str] = set()
        for bag in bags[tile_gap - block_k + 1 : tile_gap + 1]:
            left |= bag
        right: set[str] = set()
        for bag in bags[tile_gap + 1 : tile_gap + 1 + block_k]:
            right |= bag
        gap_after.append(tiles[tile_gap][1])
        scores.append(_jaccard(left, right))
    return gap_after, scores


def _depth_scores(scores: list[float]) -> list[float]:
    n = len(scores)
    depths = [0.0] * n
    for i, score in enumerate(scores):
        left_peak = score
        for j in range(i - 1, -1, -1):
            if scores[j] >= left_peak:
                left_peak = scores[j]
            else:
                break
        right_peak = score
        for j in range(i + 1, n):
            if scores[j] >= right_peak:
                right_peak = scores[j]
            else:
                break
        depths[i] = (left_peak - score) + (right_peak - score)
    return depths


def _is_local_minimum(scores: list[float], i: int) -> bool:
    n = len(scores)
    if n == 0:
        return False
    if n == 1:
        return True
    left = scores[i - 1] if i > 0 else scores[i] + 1.0
    right = scores[i + 1] if i + 1 < n else scores[i] + 1.0
    return scores[i] <= left and scores[i] <= right and (
        scores[i] < left or scores[i] < right
    )


def _zone_sizes_ok(splits: list[int], n_sentences: int, min_zone: int) -> bool:
    prev = -1
    for end in [*splits, n_sentences - 1]:
        if end - prev < min_zone:
            return False
        prev = end
    return True


def _select_boundaries(
    gap_after: list[int],
    scores: list[float],
    depths: list[float],
    n_sentences: int,
    k: float,
) -> list[int]:
    if not scores:
        return []
    if all(score >= _HIGH_SIM for score in scores):
        return []

    mean_d = statistics.fmean(depths)
    std_d = statistics.pstdev(depths) if len(depths) > 1 else 0.0
    cutoff = mean_d - k * std_d

    ranked: list[tuple[float, int]] = []
    for idx, (gap, depth, score) in enumerate(zip(gap_after, depths, scores)):
        if not _is_local_minimum(scores, idx):
            continue
        if depth <= 0 or depth <= cutoff:
            continue
        if score >= _VALLEY_MAX_SIM:
            continue
        ranked.append((-depth, gap))
    ranked.sort()

    accepted: list[int] = []
    for _, gap in ranked:
        trial = sorted([*accepted, gap])
        if _zone_sizes_ok(trial, n_sentences, _MIN_ZONE_SENTENCES):
            accepted = trial
    return accepted


def _build_zones(
    text: str,
    doc_id: str,
    sentences: list[SentenceSpan],
    splits_after: list[int],
) -> list[Zona]:
    n = len(sentences)
    bounds = [-1, *splits_after, n - 1]
    zones: list[Zona] = []
    for ordinale, (prev, end) in enumerate(zip(bounds, bounds[1:])):
        start_i = prev + 1
        first = ordinale == 0
        last = end == n - 1
        offset_inizio = 0 if first else sentences[start_i].inizio
        if last:
            offset_fine = len(text)
        else:
            offset_fine = sentences[end + 1].inizio
        testo = text[offset_inizio:offset_fine]
        zones.append(
            Zona(
                id=zona_id(doc_id, ordinale, testo),
                documento=doc_id,
                offset_inizio=offset_inizio,
                offset_fine=offset_fine,
                ordinale=ordinale,
                testo=testo,
            )
        )
    return zones


def _single_zone(text: str, doc_id: str) -> list[Zona]:
    return [
        Zona(
            id=zona_id(doc_id, 0, text),
            documento=doc_id,
            offset_inizio=0,
            offset_fine=len(text),
            ordinale=0,
            testo=text,
        )
    ]


def segmenta_zone(
    text: str,
    doc_id: str,
    *,
    window: int = 2,
    block_size: int | None = None,
) -> list[Zona]:
    """Segment ``text`` into contiguous non-overlapping lexical zones."""
    if not text or not text.strip():
        return []

    sentences = split_sentences_with_offsets(text)
    if not sentences:
        return []

    if len(sentences) < _MIN_SENTENCES:
        return _single_zone(text, doc_id)

    tile_window = max(1, window)
    tiles = _tile_spans(len(sentences), tile_window)
    block_k = block_size if block_size is not None else 2
    if block_k < 1:
        block_k = 1
    if len(tiles) < 2 * block_k:
        block_k = 1
    if len(tiles) < 2:
        return _single_zone(text, doc_id)

    sent_tokens = [_content_tokens(span.testo) for span in sentences]
    gap_after, scores = _gap_similarities(sent_tokens, tiles, block_k)
    if not scores:
        return _single_zone(text, doc_id)

    depths = _depth_scores(scores)
    splits = _select_boundaries(
        gap_after, scores, depths, len(sentences), _DEPTH_K
    )
    if not splits:
        return _single_zone(text, doc_id)
    return _build_zones(text, doc_id, sentences, splits)


__all__ = ["Zona", "segmenta_zone"]
