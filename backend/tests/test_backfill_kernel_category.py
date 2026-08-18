"""Unit tests for kernel_category backfill (Fase 13.1). No live Neo4j / OpenAI."""

from __future__ import annotations

import pytest

from app.models.kernel import EntityKernelType
from scripts.backfill_kernel_category import (
    SET_KERNEL_CATEGORY_CYPHER,
    BackfillStats,
    KernelCategoryAssignment,
    backfill_kernel_categories,
    classify_node_kernel_category,
    parse_args,
)


class FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for record in self._records:
            yield record


class FakeSession:
    """In-memory Node store: FETCH skips classified/merged; SET does not rewrite."""

    def __init__(self, nodes: list[dict]):
        self.nodes = {n["id"]: dict(n) for n in nodes}
        self.calls: list[tuple[str, dict]] = []

    def enqueue(self, records: list[dict]) -> None:
        """Unused by the backfill loop; kept so the fixture matches other FakeSessions."""
        _ = records

    async def run(self, cypher: str, **kwargs):
        self.calls.append((cypher, kwargs))
        compact = " ".join(cypher.split())
        if "SET n.kernel_category" in compact:
            node_id = kwargs["id"]
            node = self.nodes[node_id]
            if node.get("kernel_category") is None and node.get("merged_into") is None:
                node["kernel_category"] = kwargs["value"]
                return FakeResult([{"id": node_id}])
            return FakeResult([])
        if "n.kernel_category IS NULL" in compact and "SET" not in compact:
            limit = int(kwargs.get("limit") or 50)
            rows: list[dict] = []
            for node in sorted(self.nodes.values(), key=lambda item: str(item["id"])):
                if node.get("kernel_category") is not None:
                    continue
                if node.get("merged_into") is not None:
                    continue
                rows.append(
                    {
                        "id": node["id"],
                        "name": node.get("name") or "",
                        "summary": node.get("summary") or "",
                        "type": node.get("type"),
                    }
                )
                if len(rows) >= limit:
                    break
            return FakeResult(rows)
        return FakeResult([])


def _set_calls(session: FakeSession) -> list[tuple[str, dict]]:
    return [call for call in session.calls if "SET n.kernel_category" in " ".join(call[0].split())]


def _nodes() -> list[dict]:
    return [
        {
            "id": "match-1",
            "name": "Finale 2006",
            "summary": "Partita di calcio",
            "type": "event",
            "kernel_category": None,
            "merged_into": None,
        },
        {
            "id": "alice-1",
            "name": "Alice",
            "summary": "Una persona",
            "type": "entity",
            "kernel_category": "Agente",
            "merged_into": None,
        },
        {
            "id": "dup-1",
            "name": "Alice copy",
            "summary": "Duplicato fuso",
            "type": "entity",
            "kernel_category": None,
            "merged_into": "alice-1",
        },
    ]


@pytest.fixture
def classify_evento(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def fake_call_structured(
        system_prompt: str,
        user_prompt: str,
        response_model: type,
        temperature: float = 0,
        job_id: str | None = None,
    ):
        calls.append(
            {
                "system": system_prompt,
                "user": user_prompt,
                "model": response_model,
                "job_id": job_id,
            }
        )
        assert response_model is KernelCategoryAssignment
        return KernelCategoryAssignment(kernel_category=EntityKernelType.Evento)

    monkeypatch.setattr(
        "scripts.backfill_kernel_category.call_structured",
        fake_call_structured,
    )
    return calls


@pytest.mark.asyncio
async def test_unclassified_nodes_get_set(classify_evento):
    session = FakeSession(_nodes())
    stats = await backfill_kernel_categories(session)

    assert stats == BackfillStats(fetched=1, classified=1, written=1)
    assert session.nodes["match-1"]["kernel_category"] == "Evento"
    set_calls = _set_calls(session)
    assert len(set_calls) == 1
    assert set_calls[0][1]["id"] == "match-1"
    assert set_calls[0][1]["value"] == EntityKernelType.Evento.value
    assert set_calls[0][1]["value"] == "Evento"
    assert set_calls[0][1]["value"] != "E4"
    assert len(classify_evento) == 1
    assert "Finale 2006" in classify_evento[0]["user"]
    assert "Partita di calcio" in classify_evento[0]["user"]
    assert "event" in classify_evento[0]["user"]


@pytest.mark.asyncio
async def test_nodes_with_category_are_not_rewritten(classify_evento):
    session = FakeSession(_nodes())
    await backfill_kernel_categories(session)

    assert session.nodes["alice-1"]["kernel_category"] == "Agente"
    assert session.nodes["dup-1"]["kernel_category"] is None
    assert session.nodes["dup-1"]["merged_into"] == "alice-1"
    assert all(kwargs["id"] != "alice-1" for _, kwargs in _set_calls(session))
    assert all(kwargs["id"] != "dup-1" for _, kwargs in _set_calls(session))


@pytest.mark.asyncio
async def test_second_run_is_noop(classify_evento):
    session = FakeSession(_nodes())
    first = await backfill_kernel_categories(session)
    assert first.written == 1

    classify_evento.clear()
    session.calls.clear()
    second = await backfill_kernel_categories(session)

    assert second == BackfillStats(fetched=0, classified=0, written=0)
    assert classify_evento == []
    assert _set_calls(session) == []
    assert session.nodes["match-1"]["kernel_category"] == "Evento"
    assert session.nodes["alice-1"]["kernel_category"] == "Agente"


@pytest.mark.asyncio
async def test_dry_run_does_not_call_set(classify_evento):
    session = FakeSession(_nodes())
    stats = await backfill_kernel_categories(session, dry_run=True)

    assert stats.fetched == 1
    assert stats.classified == 1
    assert stats.written == 0
    assert len(classify_evento) == 1
    assert _set_calls(session) == []
    assert SET_KERNEL_CATEGORY_CYPHER not in [cypher for cypher, _ in session.calls]
    assert session.nodes["match-1"]["kernel_category"] is None


@pytest.mark.asyncio
async def test_limit_caps_work(classify_evento):
    extra = _nodes() + [
        {
            "id": "match-2",
            "name": "Finale 2010",
            "summary": "Altra partita",
            "type": "event",
            "kernel_category": None,
            "merged_into": None,
        }
    ]
    session = FakeSession(extra)
    stats = await backfill_kernel_categories(session, limit=1)

    assert stats.fetched == 1
    assert stats.written == 1
    classified = [
        nid
        for nid, node in session.nodes.items()
        if node.get("kernel_category") == "Evento"
    ]
    assert classified == ["match-1"]
    assert session.nodes["match-2"]["kernel_category"] is None


@pytest.mark.asyncio
async def test_classify_uses_call_structured(classify_evento):
    category = await classify_node_kernel_category(
        name="Finale",
        summary="Una partita",
        node_type="event",
    )
    assert category is EntityKernelType.Evento
    assert category.value == "Evento"
    assert len(classify_evento) == 1


def test_parse_args_dry_run_and_limit():
    args = parse_args(["--dry-run", "--limit", "25"])
    assert args.dry_run is True
    assert args.limit == 25
    default = parse_args([])
    assert default.dry_run is False
    assert default.limit is None
