"""Document chunking without LLM calls (tech-spec §5.1, E3.2)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

# Target ~256–512 tokens; word count used as token proxy (same heuristic as §5.1).
MIN_CHUNK_WORDS = 256
TARGET_CHUNK_WORDS = 384
MAX_CHUNK_WORDS = 512
OVERLAP_RATIO = 0.125  # 12.5%, within 10–15% spec range
CHUNK_OVERLAP_WORDS = int(TARGET_CHUNK_WORDS * OVERLAP_RATIO)
SHORT_TEXT_WORD_THRESHOLD = 256


@dataclass
class Chunk:
    id: str
    doc_id: str
    text: str


def _word_count(text: str) -> int:
    return len(text.split())


def _new_chunk(doc_id: str, text: str) -> Chunk:
    return Chunk(id=str(uuid.uuid4()), doc_id=doc_id, text=text.strip())


def _is_structured_short_lines(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return len(lines) > 1 and all(_word_count(line) < 80 for line in lines)


def _split_sentences(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if _is_structured_short_lines(text):
        return lines
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _word_window_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(words) <= chunk_size:
        return [text.strip()]

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _merge_parts(parts: list[str], separator: str, chunk_size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            chunks.append(separator.join(current).strip())

    for index, part in enumerate(parts):
        candidate_parts = [*current, part]
        candidate = separator.join(candidate_parts).strip()
        if current and _word_count(candidate) > chunk_size:
            flush()
            current = [part]
        else:
            current = candidate_parts

    flush()

    if len(chunks) == 1 and _word_count(chunks[0]) > MAX_CHUNK_WORDS:
        return _word_window_split(chunks[0], chunk_size, overlap)

    expanded: list[str] = []
    for chunk in chunks:
        if _word_count(chunk) > MAX_CHUNK_WORDS:
            expanded.extend(_recursive_split(chunk))
        else:
            expanded.append(chunk)
    return expanded


def _recursive_split(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if _word_count(text) <= MAX_CHUNK_WORDS:
        return [text]

    for separator in ("\n\n", "\n", ". ", " "):
        if separator not in text:
            continue
        parts = text.split(separator)
        if len(parts) <= 1:
            continue
        merged = _merge_parts(parts, separator, TARGET_CHUNK_WORDS, CHUNK_OVERLAP_WORDS)
        if merged and all(_word_count(chunk) <= MAX_CHUNK_WORDS for chunk in merged):
            return merged

    return _word_window_split(text, TARGET_CHUNK_WORDS, CHUNK_OVERLAP_WORDS)


def _chunk_short_text(text: str, doc_id: str) -> list[Chunk]:
    if _is_structured_short_lines(text):
        return [_new_chunk(doc_id, part) for part in _split_sentences(text)]
    return [_new_chunk(doc_id, text)]


def chunk_text(text: str, doc_id: str) -> list[Chunk]:
    """Split document text into chunks with stable UUID ids."""
    normalized = text.strip()
    if not normalized:
        return []

    if _word_count(normalized) < SHORT_TEXT_WORD_THRESHOLD:
        return _chunk_short_text(normalized, doc_id)

    raw_chunks = _recursive_split(normalized)
    return [_new_chunk(doc_id, chunk) for chunk in raw_chunks if chunk.strip()]
