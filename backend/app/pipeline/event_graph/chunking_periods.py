"""Period-safe chunker (piano sez. 9 / spec §13).

Packs whole periods up to ``EVENT_GRAPH_TARGET_CHUNK_WORDS`` with **no overlap**
and **no mid-period split**. A single period longer than
``EVENT_GRAPH_MAX_CHUNK_WORDS`` is emitted intact (period-safe beats the cap).

Ids/ordinals are assigned on the packed sequence, then chunks without a finite
verb are discarded (gaps in ``ordinale`` are allowed so ids stay stable).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.pipeline.event_graph.config import settings
from app.pipeline.event_graph.ids import eg_chunk_id
from app.pipeline.event_graph.text_norm import fold_text

# Closing quotes that may follow a terminator without breaking the period.
_CLOSING_QUOTES = "\"'»”’"
_TERMINATORS = ".?!"

# Single-token abbreviations (period stripped, matched case-insensitively).
_ABBREVS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "ecc",
        "inc",
        "ltd",
        "corp",
        "approx",
        "cf",
        "al",
        "no",
        "vol",
        "fig",
        "pp",
        "ch",
        "dept",
        "n",
        "art",
        "sig",
        "dott",
        "pag",
        "cfr",
        "avv",
        "ing",
        "geom",
        "on",
        "cap",
        "ca",
        "es",
        "ndr",
        "ted",
        "ingl",
        "gen",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
        "genn",
        "febb",
        "magg",
        "sett",
        "ott",
        "dic",
        "p",
        "eng",
        "rev",
        "hon",
        "mt",
        "ft",
        "oz",
        "lb",
        "kg",
        "km",
        "cm",
        "mm",
        "co",
        "assn",
        "bros",
        "ave",
        "blvd",
        "rd",
        "univ",
        "govt",
        "sgt",
        "cpl",
        "col",
        "lt",
        "adm",
        "capt",
        "sg",
        "dottssa",
        "sig.ra",
        "spa",
        "srl",
        "n°",
    }
)

_MULTI_DOT_ABBREVS = frozenset(
    {
        "e.g",
        "i.e",
        "p.es",
        "u.s",
        "u.k",
        "n.d.r",
        "p.s",
        "a.d",
        "b.c",
        "ph.d",
        "m.d",
        "b.a",
        "m.a",
        "n.b",
        "q.e.d",
    }
)

_EN_AUX = frozenset(
    {
        "am",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "can",
        "could",
        "may",
        "might",
        "shall",
        "should",
        "must",
    }
)

_IT_AUX = frozenset(
    {
        "è",
        "e'",
        "sono",
        "sei",
        "siamo",
        "siete",
        "era",
        "ero",
        "eravamo",
        "erano",
        "fui",
        "fu",
        "fummo",
        "furono",
        "ha",
        "hai",
        "ho",
        "abbiamo",
        "avete",
        "hanno",
        "aveva",
        "avevo",
        "avevano",
        "sarà",
        "saranno",
        "deve",
        "devono",
        "può",
        "possono",
        "vuole",
        "vogliono",
        "fa",
        "fanno",
        "va",
        "vanno",
        "sta",
        "stanno",
    }
)

_IT_ENDING = re.compile(
    r"(avo|avi|ava|avamo|avate|avano|isco|isci|isce|iamo|iate|iscono|"
    r"erò|erai|erà|eremo|erete|eranno|erei|erebbe|arono|ito|ati)$",
    re.IGNORECASE,
)

# Distinctive finite-looking Italian endings (len>=5 enforced by the caller).
_CLEAR_VERBISH = re.compile(r"(ava|ì|ò|é|erà|ono|iamo)$", re.IGNORECASE)

_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ''']+")


@dataclass(frozen=True)
class PeriodChunk:
    id: str
    doc_id: str
    ordinale: int
    testo: str


@dataclass(frozen=True)
class SentenceSpan:
    """A sentence as an exact slice of the original document string."""

    testo: str
    inizio: int
    fine: int


def _word_count(text: str) -> int:
    return len(text.split())


def _join_periods(periods: list[str]) -> str:
    return " ".join(period.strip() for period in periods if period.strip())


def _preceding_token(text: str, period_index: int) -> str:
    j = period_index - 1
    while j >= 0 and text[j] in _CLOSING_QUOTES:
        j -= 1
    end = j + 1
    while j >= 0 and (text[j].isalpha() or text[j] in ".'’"):
        j -= 1
    return text[j + 1 : end].lower().rstrip(_CLOSING_QUOTES)


def _is_abbrev_period(text: str, period_index: int) -> bool:
    token = _preceding_token(text, period_index)
    if not token:
        return False
    if token in _ABBREVS or token in _MULTI_DOT_ABBREVS:
        return True
    if len(token) == 1 and token.isalpha():
        return True
    return False


def _is_list_ordinal_period(text: str, period_index: int) -> bool:
    """True for a line-initial ``1. `` / ``11. `` marker, not ``1987. Then``.

    Decimals (``3.14``) never reach here: the splitter only breaks a period
    when the next character is space or end-of-text.
    """
    j = period_index - 1
    if j < 0 or not text[j].isdigit():
        return False
    while j >= 0 and text[j].isdigit():
        j -= 1
    k = j
    while k >= 0 and text[k] in " \t":
        k -= 1
    if k >= 0 and text[k] != "\n":
        return False
    nxt = period_index + 1
    return nxt >= len(text) or text[nxt].isspace()


def _split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _TERMINATORS:
            if ch == ".":
                if i + 1 < n and text[i + 1].isalpha():
                    i += 1
                    continue
                if _is_abbrev_period(text, i) or _is_list_ordinal_period(text, i):
                    i += 1
                    continue
            while i + 1 < n and text[i + 1] in _TERMINATORS:
                i += 1
            while i + 1 < n and text[i + 1] in _CLOSING_QUOTES:
                i += 1
            if i + 1 >= n or text[i + 1].isspace():
                sent = text[start : i + 1].strip()
                if sent:
                    sentences.append(sent)
                i += 1
                while i < n and text[i].isspace():
                    i += 1
                start = i
                continue
        i += 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _split_periods(text: str) -> list[str]:
    periods: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        paragraph = paragraph.strip()
        if paragraph:
            periods.extend(_split_sentences(paragraph))
    return periods


def split_sentences_with_offsets(text: str) -> list[SentenceSpan]:
    """Split ``text`` into sentences with absolute offsets into the original string.

    Reuses the period splitter (abbreviations, decimals, paragraph breaks).
    Does not change ``split()`` packing. Each span is an exact slice:
    ``text[inizio:fine] == testo``.
    """
    if not text or not text.strip():
        return []

    spans: list[SentenceSpan] = []
    cursor = 0
    for sent in _split_periods(text):
        idx = text.find(sent, cursor)
        if idx < 0:
            # Conservative: do not invent a boundary if the stripped sentence
            # is not a literal substring (should not happen with this splitter).
            continue
        fine = idx + len(sent)
        spans.append(SentenceSpan(testo=text[idx:fine], inizio=idx, fine=fine))
        cursor = fine
    return spans


def _pack(periods: list[str], target: int, max_words: int) -> list[str]:
    if not periods:
        return []

    chunks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            chunks.append(_join_periods(current))
            current.clear()

    for period in periods:
        period_words = _word_count(period)
        if not current:
            current.append(period)
            if period_words > max_words:
                flush()
            continue
        candidate_words = _word_count(_join_periods([*current, period]))
        if candidate_words > target:
            flush()
            current.append(period)
            if period_words > max_words:
                flush()
        else:
            current.append(period)
    flush()
    return chunks


def _norm_token(token: str) -> str:
    return token.lower().replace("\u2019", "'").replace("\u2018", "'")


def ha_verbo_finito(testo_or_chunk: str | PeriodChunk) -> bool:
    """Conservative finite-verb gate (IT+EN). When unsure, keep the chunk."""
    testo = (
        testo_or_chunk.testo
        if isinstance(testo_or_chunk, PeriodChunk)
        else testo_or_chunk
    )
    tokens = _TOKEN_RE.findall(testo)
    if not tokens:
        return False

    norms = [_norm_token(token) for token in tokens]
    has_aux = any(norm in _EN_AUX or norm in _IT_AUX for norm in norms)
    if has_aux:
        return True

    has_clear_verbish = False
    for norm in norms:
        if len(norm) >= 4 and _IT_ENDING.search(norm):
            return True
        if len(norm) >= 5 and norm.endswith("ed") and norm.isascii():
            return True
        if len(norm) >= 5 and _CLEAR_VERBISH.search(norm):
            has_clear_verbish = True

    # Present 3rd-person -a/-e/-ono counts only with a clear verb-ish companion
    # (aux already returned above). Bare noun phrases stay discarded.
    if has_clear_verbish:
        return True

    return False


def split(text: str, doc_id: str) -> list[PeriodChunk]:
    """Pack periods, assign stable ids, then drop verb-less chunks."""
    if not text or not text.strip():
        return []

    target = settings.EVENT_GRAPH_TARGET_CHUNK_WORDS
    max_words = settings.EVENT_GRAPH_MAX_CHUNK_WORDS
    packed_texts = _pack(_split_periods(text), target, max_words)

    packed: list[PeriodChunk] = []
    for ordinale, testo in enumerate(packed_texts):
        packed.append(
            PeriodChunk(
                id=eg_chunk_id(doc_id, ordinale, testo),
                doc_id=doc_id,
                ordinale=ordinale,
                testo=testo,
            )
        )
    return [chunk for chunk in packed if ha_verbo_finito(chunk)]


# ---------------------------------------------------------------------------
# Stage 0 MICRO: dialogue/narrative units inside a zone (Addendum 2 M3a-0)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnitaTesto:
    testo: str
    offset_inizio: int  # absolute in the DOCUMENT
    offset_fine: int
    tipo: Literal["narrativa", "dialogo"]
    connettivo_confine: str | None  # connective at the left boundary
    zona_id: str | None = None
    indice: int = 0  # order within the zone


# Closed bilingual boundary connectives (extraction rubric §8.1 / §10).
# Longest phrase first so "anche se" wins over "se".
_BOUNDARY_CONNECTIVES: tuple[str, ...] = tuple(
    sorted(
        {
            "affinché",
            "affinchè",
            "anche se",
            "and",
            "as long as",
            "as soon as",
            "as",
            "because",
            "before",
            "benché",
            "benchè",
            "but",
            "consequently",
            "così",
            "dato che",
            "dopo che",
            "dopo",
            "dunque",
            "e",
            "even if",
            "eppure",
            "finché",
            "finchè",
            "fino a che",
            "given that",
            "hence",
            "however",
            "if",
            "in case",
            "in order to",
            "in quanto",
            "invece",
            "ma",
            "mentre",
            "nonostante",
            "perché",
            "perchè",
            "perche",
            "perciò",
            "percio",
            "però",
            "pero",
            "poiché",
            "poichè",
            "poi",
            "provided that",
            "qualora",
            "quando",
            "quindi",
            "se",
            "sebbene",
            "siccome",
            "since",
            "so that",
            "so",
            "then",
            "therefore",
            "though",
            "thus",
            "tuttavia",
            "until",
            "when",
            "whereas",
            "while",
            "yet",
            "a patto che",
            "prima che",
            "prima di",
            "prima",
            "after",
            "afterwards",
            "although",
            "unless",
            "pertanto",
        },
        key=len,
        reverse=True,
    )
)
_BOUNDARY_SINGLE = frozenset(
    phrase for phrase in _BOUNDARY_CONNECTIVES if " " not in phrase
)

_OPEN_TO_CLOSE = {"«": "»", "“": "”", "‘": "’"}
_SYMMETRIC_QUOTES = frozenset({'"'})
_QUOTE_OPENERS = frozenset(_OPEN_TO_CLOSE) | _SYMMETRIC_QUOTES
_QUOTE_CLOSERS = frozenset(_OPEN_TO_CLOSE.values())
_LEADING_JUNK = re.compile(r"^[\s,;]+")


def _stripped_bounds(text: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start >= end:
        return None
    return start, end


def _quote_scan(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Scan quotes. status: 'ok' | 'unclosed' | 'ambiguous'."""
    spans: list[tuple[int, int]] = []
    opener: str | None = None
    start = -1
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if opener is None:
            if ch in _QUOTE_OPENERS:
                opener = ch
                start = i
            elif ch in _QUOTE_CLOSERS and ch != "’":
                return "ambiguous", []
            i += 1
            continue
        closer = _OPEN_TO_CLOSE.get(opener, opener)
        if ch == closer:
            spans.append((start, i + 1))
            opener = None
            start = -1
            i += 1
            continue
        if ch in _QUOTE_OPENERS:
            return "ambiguous", []
        if ch in _QUOTE_CLOSERS and ch != "’" and ch != closer:
            return "ambiguous", []
        i += 1
    if opener is not None:
        return "unclosed", []
    return "ok", spans


def _merge_quote_spans(spans: list[SentenceSpan], text: str) -> list[SentenceSpan]:
    """Join consecutive sentences while a quote is still unclosed. Do not invent."""
    if not spans:
        return []
    merged: list[SentenceSpan] = []
    i = 0
    while i < len(spans):
        start = spans[i].inizio
        end = spans[i].fine
        j = i
        while _quote_scan(text[start:end])[0] == "unclosed" and j + 1 < len(spans):
            j += 1
            end = spans[j].fine
        bounds = _stripped_bounds(text, start, end)
        if bounds is not None:
            a, b = bounds
            merged.append(SentenceSpan(testo=text[a:b], inizio=a, fine=b))
        i = j + 1
    return merged


def _carve_dialogue(
    span: SentenceSpan,
) -> list[tuple[int, int, Literal["narrativa", "dialogo"]]]:
    """Yield (inizio, fine, tipo) into zona.testo. Ambiguous → one narrativa."""
    status, quotes = _quote_scan(span.testo)
    rel = span.inizio
    if status != "ok" or not quotes:
        return [(span.inizio, span.fine, "narrativa")]

    pieces: list[tuple[int, int, Literal["narrativa", "dialogo"]]] = []
    cursor = 0
    for qs, qe in quotes:
        if qs > cursor:
            left = _stripped_bounds(span.testo, cursor, qs)
            if left is not None:
                pieces.append((rel + left[0], rel + left[1], "narrativa"))
        pieces.append((rel + qs, rel + qe, "dialogo"))
        cursor = qe
    if cursor < len(span.testo):
        right = _stripped_bounds(span.testo, cursor, len(span.testo))
        if right is not None:
            pieces.append((rel + right[0], rel + right[1], "narrativa"))
    return pieces or [(span.inizio, span.fine, "narrativa")]


def _leading_connective(testo: str) -> str | None:
    rest = _LEADING_JUNK.sub("", testo)
    if not rest:
        return None
    lower = rest.lower()
    for phrase in _BOUNDARY_CONNECTIVES:
        if not lower.startswith(phrase):
            continue
        end = len(phrase)
        if end < len(rest) and (rest[end].isalnum() or rest[end] in "'’"):
            continue
        return rest[:end]
    return None


def _trailing_connective(testo: str) -> str | None:
    tokens = _TOKEN_RE.findall(testo)
    if not tokens:
        return None
    last = tokens[-1]
    if last.lower() in _BOUNDARY_SINGLE:
        return last
    return None


def connettivo_confine(testo: str, precedente: str | None = None) -> str | None:
    """Connective visible at the left boundary of ``testo``.

    Prefers a leading connective on ``testo``; otherwise a trailing
    connective on ``precedente`` that introduces the next unit.
    """
    leading = _leading_connective(testo)
    if leading is not None:
        return leading
    if not precedente:
        return None
    trailing = _trailing_connective(precedente)
    if trailing is None:
        return None
    # Only if the previous unit actually ends with that token (introduces next).
    stripped = precedente.rstrip()
    if not stripped:
        return None
    # Strip trailing punctuation so "but," still counts as trailing connective.
    tail = stripped.rstrip(".,;:!?»\"”’'")
    if not tail:
        return None
    last = _TOKEN_RE.findall(tail)
    if last and last[-1] == trailing:
        return trailing
    return None


def _connettivo_confine(testo: str, previous: UnitaTesto | None) -> str | None:
    prev_text = previous.testo if previous is not None else None
    return connettivo_confine(testo, prev_text)


def preprocess_zona(zona: object, document_text: str | None = None) -> list[UnitaTesto]:
    """Segment zona.testo into narrative/dialogue units with document offsets."""
    testo = getattr(zona, "testo", None) or ""
    if not testo.strip():
        return []

    base = int(getattr(zona, "offset_inizio", 0) or 0)
    zona_id = getattr(zona, "id", None)
    if zona_id is not None:
        zona_id = str(zona_id)

    sentences = split_sentences_with_offsets(testo)
    regions = _merge_quote_spans(sentences, testo)

    raw: list[UnitaTesto] = []
    previous: UnitaTesto | None = None
    indice = 0
    for region in regions:
        for local_start, local_end, tipo in _carve_dialogue(region):
            piece = testo[local_start:local_end]
            abs_start = base + local_start
            abs_end = base + local_end
            if document_text is not None:
                excerpt = document_text[abs_start:abs_end]
                if excerpt == piece:
                    piece = excerpt
            piece = fold_text(piece)
            unit = UnitaTesto(
                testo=piece,
                offset_inizio=abs_start,
                offset_fine=abs_end,
                tipo=tipo,
                connettivo_confine=_connettivo_confine(piece, previous),
                zona_id=zona_id,
                indice=indice,
            )
            raw.append(unit)
            previous = unit
            indice += 1
    return raw


__all__ = [
    "PeriodChunk",
    "SentenceSpan",
    "UnitaTesto",
    "connettivo_confine",
    "ha_verbo_finito",
    "preprocess_zona",
    "split",
    "split_sentences_with_offsets",
]
