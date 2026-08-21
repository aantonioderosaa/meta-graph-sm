"""Macrotask 3: append_node_revision. FakeSession, no Neo4j, no OpenAI."""

from __future__ import annotations

import pytest

from app.pipeline.event_slots import (
    APPEND_NODE_REVISION_CYPHER,
    READ_NODE_SCALAR_CYPHER,
    append_node_revision,
)
from tests.test_event_slots import (
    EVENT_ID,
    HEAD,
    RUN_ID,
    FakeSession,
    SlotGraph,
)


def _summary_graph(summary: str = "ok") -> SlotGraph:
    graph = SlotGraph()
    graph.add_node(HEAD, "esperimento 5", summary=summary)
    return graph


@pytest.mark.asyncio
async def test_append_node_revision_twice_keeps_prior_old_value():
    graph = _summary_graph("ok")
    session = FakeSession(graph)

    await append_node_revision(
        session,
        HEAD,
        property="summary",
        new_value="fallato",
        event_id=EVENT_ID,
        run_id=RUN_ID,
    )
    first_old = graph.nodes[HEAD]["revisions"][-1]["old_value"]
    assert first_old == "ok"
    assert graph.nodes[HEAD]["summary"] == "fallato"
    assert len(graph.nodes[HEAD]["revisions"]) == 1

    await append_node_revision(
        session,
        HEAD,
        property="summary",
        new_value="ripetuto",
        event_id="evento-2",
        run_id="run-2",
    )
    revisions = graph.nodes[HEAD]["revisions"]
    assert graph.nodes[HEAD]["summary"] == "ripetuto"
    assert len(revisions) == 2
    assert revisions[-1]["old_value"] == "fallato"
    assert revisions[-1]["property"] == "summary"
    assert revisions[-1]["event_id"] == "evento-2"
    assert revisions[-1]["run_id"] == "run-2"
    assert revisions[-1]["at"]
    assert revisions[0]["old_value"] == first_old == "ok"
    assert revisions[0]["event_id"] == EVENT_ID


@pytest.mark.asyncio
async def test_append_node_revision_empty_event_or_run_raises():
    session = FakeSession(_summary_graph())
    with pytest.raises(ValueError, match="event_id"):
        await append_node_revision(
            session, HEAD, property="summary", new_value="x", event_id="", run_id=RUN_ID
        )
    with pytest.raises(ValueError, match="run_id"):
        await append_node_revision(
            session, HEAD, property="summary", new_value="x", event_id=EVENT_ID, run_id="  "
        )
    assert session.graph.nodes[HEAD]["summary"] == "ok"
    assert session.graph.nodes[HEAD]["revisions"] == []


@pytest.mark.asyncio
async def test_append_node_revision_refuses_identity_and_missing_node():
    session = FakeSession(_summary_graph())
    with pytest.raises(ValueError, match="identity"):
        await append_node_revision(
            session, HEAD, property="id", new_value="other", event_id=EVENT_ID, run_id=RUN_ID
        )
    with pytest.raises(ValueError, match="not found"):
        await append_node_revision(
            session,
            "missing-node",
            property="summary",
            new_value="x",
            event_id=EVENT_ID,
            run_id=RUN_ID,
        )
    assert session.graph.nodes[HEAD]["id"] == HEAD


@pytest.mark.asyncio
async def test_append_node_revision_uses_read_then_additive_set():
    session = FakeSession(_summary_graph("prima"))
    await append_node_revision(
        session, HEAD, property="summary", new_value="dopo", event_id=EVENT_ID, run_id=RUN_ID
    )
    cyphers = [cy for cy, _ in session.calls]
    assert cyphers == [READ_NODE_SCALAR_CYPHER, APPEND_NODE_REVISION_CYPHER]
    assert "DELETE" not in APPEND_NODE_REVISION_CYPHER
    assert "n.revisions +" in APPEND_NODE_REVISION_CYPHER
    params = session.calls[1][1]
    assert params["revision"]["old_value"] == "prima"
    assert params["new_value"] == "dopo"
