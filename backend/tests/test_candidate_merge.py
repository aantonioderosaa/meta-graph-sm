"""R1.4 — merge/dedup/cap of relation candidates."""

from __future__ import annotations

from app.pipeline.relations import MAX_TOTAL_CANDIDATES, Candidate, merge_and_cap_candidates


def test_duplicate_id_prefers_embedding_via_and_keeps_score():
    embedding = [
        Candidate(id="shared", text="from emb", score=0.91, via="embedding"),
    ]
    chunk = [
        Candidate(id="shared", text="from chunk", score=None, via="chunk"),
    ]
    doc = [
        Candidate(id="shared", text="from doc", score=None, via="doc"),
    ]

    merged = merge_and_cap_candidates(embedding, chunk, doc)

    assert len(merged) == 1
    assert merged[0].id == "shared"
    assert merged[0].via == "embedding"
    assert merged[0].score == 0.91
    assert merged[0].text == "from emb"


def test_cap_truncates_in_embedding_chunk_doc_order():
    embedding = [
        Candidate(id=f"e{i}", text=f"e{i}", score=0.9 - i * 0.01, via="embedding")
        for i in range(12)
    ]
    chunk = [
        Candidate(id=f"c{i}", text=f"c{i}", score=None, via="chunk") for i in range(8)
    ]
    doc = [
        Candidate(id=f"d{i}", text=f"d{i}", score=None, via="doc") for i in range(8)
    ]

    merged = merge_and_cap_candidates(embedding, chunk, doc)

    assert len(merged) == MAX_TOTAL_CANDIDATES
    via_counts = {label: 0 for label in ("embedding", "chunk", "doc")}
    for candidate in merged:
        via_counts[candidate.via] += 1

    # 12 embedding + 8 chunk = 20; all doc dropped
    assert via_counts["embedding"] == 12
    assert via_counts["chunk"] == 8
    assert via_counts["doc"] == 0
    assert all(c.id.startswith("e") or c.id.startswith("c") for c in merged)


def test_chunk_beats_doc_when_same_id():
    chunk = [Candidate(id="x", text="chunk", score=None, via="chunk")]
    doc = [Candidate(id="x", text="doc", score=None, via="doc")]

    merged = merge_and_cap_candidates([], chunk, doc)

    assert len(merged) == 1
    assert merged[0].via == "chunk"
