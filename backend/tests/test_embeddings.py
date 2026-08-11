"""Embedding module tests (E3.1)."""

from __future__ import annotations

import pytest

from app.pipeline import embeddings


@pytest.fixture(scope="module")
def embedding_model():
    """Load the real embedding model once per module."""
    try:
        vector = embeddings.embed("warmup")
    except Exception as exc:  # pragma: no cover - network/model issues
        pytest.skip(f"Embedding model unavailable: {exc}")
    assert len(vector) == 768
    return True


def test_embed_returns_768_dimensions(embedding_model):
    vector = embeddings.embed("testo di prova")
    assert len(vector) == 768
    assert all(isinstance(value, float) for value in vector)


def test_embed_is_deterministic(embedding_model):
    first = embeddings.embed("same input text")
    second = embeddings.embed("same input text")
    assert first == second


def test_embed_batch_matches_single(embedding_model):
    texts = ["alpha", "beta"]
    batch = embeddings.embed_batch(texts)
    assert len(batch) == 2
    for index, text in enumerate(texts):
        single = embeddings.embed(text)
        assert len(batch[index]) == len(single)
        for left, right in zip(batch[index], single, strict=True):
            assert left == pytest.approx(right, rel=1e-5, abs=1e-5)
