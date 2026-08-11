"""Chunking module tests (E3.2)."""

from __future__ import annotations

from app.pipeline.chunking import (
    CHUNK_OVERLAP_WORDS,
    MAX_CHUNK_WORDS,
    MIN_CHUNK_WORDS,
    chunk_text,
)


def _word_count(text: str) -> int:
    return len(text.split())


def _long_document(paragraph_words: int = 120, paragraphs: int = 20) -> str:
    paragraph = " ".join(f"token{i}" for i in range(paragraph_words))
    return "\n\n".join([paragraph] * paragraphs)


def test_long_text_produces_chunks_in_target_range_with_overlap():
    text = _long_document()
    chunks = chunk_text(text, "doc-long")
    assert len(chunks) > 1

    sizes = [_word_count(chunk.text) for chunk in chunks]
    assert all(size <= MAX_CHUNK_WORDS for size in sizes)
    for index, size in enumerate(sizes[:-1]):
        assert MIN_CHUNK_WORDS <= size <= MAX_CHUNK_WORDS, f"chunk {index} size={size}"
    if len(sizes) > 1:
        assert sizes[-1] <= MAX_CHUNK_WORDS

    first_words = chunks[0].text.split()
    second_words = chunks[1].text.split()
    tail = set(first_words[-CHUNK_OVERLAP_WORDS:])
    head = set(second_words[: CHUNK_OVERLAP_WORDS * 2])
    overlap_count = len(tail & head)
    min_expected = int(CHUNK_OVERLAP_WORDS * 0.1)
    assert overlap_count >= min_expected


def test_short_text_produces_single_chunk():
    text = "Alice works at Acme Corp in Berlin since 2020."
    chunks = chunk_text(text, "doc-short")
    assert len(chunks) == 1
    assert chunks[0].doc_id == "doc-short"
    assert chunks[0].text == text


def test_structured_short_lines_use_sentence_split_path():
    text = (
        "Alice works at Acme.\n"
        "Bob prefers dark chocolate.\n"
        "Carol moved to Rome in 2023."
    )
    chunks = chunk_text(text, "doc-structured")
    assert len(chunks) == 3
    assert {chunk.text for chunk in chunks} == {
        "Alice works at Acme.",
        "Bob prefers dark chocolate.",
        "Carol moved to Rome in 2023.",
    }


def test_chunk_ids_are_unique():
    chunks = chunk_text(_long_document(), "doc-ids")
    ids = [chunk.id for chunk in chunks]
    assert len(ids) == len(set(ids))
