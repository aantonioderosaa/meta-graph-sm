"""Local embedding via sentence-transformers (tech-spec §5.1, E3.1).

First load of ``BAAI/bge-base-en-v1.5`` downloads weights (~400 MB) and initializes
the model; expect several seconds on CPU at cold start. Subsequent calls reuse the
singleton loaded at module level.
"""

from __future__ import annotations

from sentence_transformers import SentenceTransformer

from app.core.config import settings

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(settings.EMBEDDING_MODEL)
    return _model


def reset_embedding_model() -> None:
    """Clear cached model (for tests)."""
    global _model
    _model = None


def embed(text: str) -> list[float]:
    """Return a 768-dim L2-normalized embedding for a single text."""
    vector = _get_model().encode(text, normalize_embeddings=True)
    return vector.tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Return L2-normalized embeddings for a batch of texts."""
    if not texts:
        return []
    vectors = _get_model().encode(texts, normalize_embeddings=True)
    return [row.tolist() for row in vectors]
