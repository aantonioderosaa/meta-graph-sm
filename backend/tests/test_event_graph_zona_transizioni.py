"""MT3 / Parte A — zona transition summaries (no Docker, no live LLM)."""

from __future__ import annotations

import pytest

from app.models.event_graph import TransizioneZonaResult
from app.pipeline.event_graph.persistence import _merge_successione_zona
from app.pipeline.event_graph.zona_segmentation import Zona
from app.pipeline.event_graph.zona_transizioni import (
    genera_transizione,
    genera_transizioni_zona,
)


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.runs = []

    async def run(self, query, parameters=None, **params):
        merged = dict(params)
        if parameters is not None and isinstance(parameters, dict):
            merged = {**parameters, **merged}
        self.runs.append((query, merged))

        class R:
            def data(self_inner):
                return self.rows

        return R()


def _zona(
    zona_id: str,
    ordinale: int,
    riassunto: str,
    *,
    espansa: bool = True,
    testo: str = "TESTO GREZZO CHE NON DEVE COMPARIRE",
) -> Zona:
    return Zona(
        id=zona_id,
        documento="doc-transizioni",
        offset_inizio=ordinale * 10,
        offset_fine=ordinale * 10 + 9,
        ordinale=ordinale,
        testo=testo,
        riassunto=riassunto,
        espansa=espansa,
    )


def _assert_no_delete(session: FakeSession) -> None:
    for query, _ in session.runs:
        upper = query.upper()
        assert "DELETE" not in upper
        assert "DETACH" not in upper
        assert "REMOVE " not in upper


@pytest.mark.asyncio
async def test_a_u1_genera_transizione_returns_riassunto(monkeypatch):
    captured: dict[str, object] = {}

    async def stub(system_prompt, user_prompt, response_model, temperature=0, job_id=None):
        captured["user"] = user_prompt
        captured["model"] = response_model
        captured["temperature"] = temperature
        assert temperature == 0
        assert response_model is TransizioneZonaResult
        return TransizioneZonaResult(riassunto="Il contesto passa dal bosco al villaggio.")

    monkeypatch.setattr(
        "app.pipeline.event_graph.zona_transizioni.call_structured",
        stub,
    )
    zona_a = _zona("z-a", 0, "Il viandante entra nel bosco.")
    zona_b = _zona("z-b", 1, "Arriva al villaggio e chiede alloggio.")
    result = await genera_transizione(zona_a, zona_b, job_id="job-t")
    assert result is not None
    assert result.riassunto
    assert result.riassunto == "Il contesto passa dal bosco al villaggio."
    user = str(captured["user"])
    assert "Il viandante entra nel bosco." in user
    assert "Arriva al villaggio e chiede alloggio." in user
    assert "TESTO GREZZO CHE NON DEVE COMPARIRE" not in user


@pytest.mark.asyncio
async def test_a_u2_genera_transizioni_zona_best_effort_per_pair(monkeypatch):
    calls: list[str] = []

    async def stub(system_prompt, user_prompt, response_model, temperature=0, job_id=None):
        calls.append(user_prompt)
        if len(calls) == 1:
            raise RuntimeError("llm down")
        return TransizioneZonaResult(riassunto="Cambia il luogo, si entra in città.")

    monkeypatch.setattr(
        "app.pipeline.event_graph.zona_transizioni.call_structured",
        stub,
    )
    zone = [
        _zona("z-0", 0, "Prima zona: il bosco."),
        _zona("z-1", 1, "Seconda zona: il sentiero."),
        _zona("z-2", 2, "Terza zona: la città."),
    ]
    result = await genera_transizioni_zona(zone, job_id="job-t")
    assert calls == [
        "Prima zona: il bosco.\n\nSeconda zona: il sentiero.",
        "Seconda zona: il sentiero.\n\nTerza zona: la città.",
    ]
    assert result == {
        ("z-0", "z-1"): "",
        ("z-1", "z-2"): "Cambia il luogo, si entra in città.",
    }


@pytest.mark.asyncio
async def test_a_u3_merge_successione_zona_idempotent():
    session = FakeSession()
    first = await _merge_successione_zona(
        session, "z-a", "z-b", "Dal bosco al villaggio.", "job-1"
    )
    second = await _merge_successione_zona(
        session, "z-a", "z-b", "Dal bosco al villaggio.", "job-1"
    )
    assert len(session.runs) == 2
    query_a, params_a = session.runs[0]
    query_b, params_b = session.runs[1]
    assert query_a == query_b == first == second
    assert params_a == params_b
    assert params_a["id_a"] == "z-a"
    assert params_a["id_b"] == "z-b"
    blob = query_a
    assert "SUCCESSIONE_ZONA" in blob
    assert "riassunto_transizione" in blob
    assert "MATCH (za:Zona" in blob
    assert "MATCH (zb:Zona" in blob
    assert "MERGE (za)-[r:SUCCESSIONE_ZONA]->(zb)" in blob
    assert "MERGE (za:Zona" not in blob
    _assert_no_delete(session)


@pytest.mark.asyncio
async def test_arco_strutturale_anche_se_riassunto_vuoto_e_llm_fallisce(monkeypatch):
    async def stub(system_prompt, user_prompt, response_model, temperature=0, job_id=None):
        raise RuntimeError("llm down")

    monkeypatch.setattr(
        "app.pipeline.event_graph.zona_transizioni.call_structured",
        stub,
    )
    zone = [
        _zona("z-0", 0, ""),
        _zona("z-1", 1, ""),
        _zona("z-2", 2, "", espansa=False),
    ]
    result = await genera_transizioni_zona(zone, job_id="job-t")
    assert result == {("z-0", "z-1"): ""}
    assert ("z-1", "z-2") not in result


@pytest.mark.asyncio
async def test_transizione_usa_testo_se_riassunto_vuoto(monkeypatch):
    captured: list[str] = []

    async def stub(system_prompt, user_prompt, response_model, temperature=0, job_id=None):
        captured.append(user_prompt)
        return TransizioneZonaResult(riassunto="Cambia il soggetto.")

    monkeypatch.setattr(
        "app.pipeline.event_graph.zona_transizioni.call_structured",
        stub,
    )
    zone = [
        _zona("z-0", 0, "", testo="Il meccanico trova l'auto."),
        _zona("z-1", 1, "", testo="Smonta il motore."),
    ]
    result = await genera_transizioni_zona(zone, job_id="job-t")
    assert captured
    assert "Il meccanico trova l'auto." in captured[0]
    assert "Smonta il motore." in captured[0]
    assert result == {("z-0", "z-1"): "Cambia il soggetto."}


@pytest.mark.asyncio
async def test_merge_successione_con_testo_vuoto():
    session = FakeSession()
    await _merge_successione_zona(session, "z-a", "z-b", "", "job-1")
    assert session.runs
    query, params = session.runs[0]
    assert "SUCCESSIONE_ZONA" in query
    assert params["riassunto"] == ""


@pytest.mark.asyncio
async def test_persisti_transizioni_scrive_arco_senza_testo():
    from app.pipeline.event_graph.persistence import persisti_transizioni_zona

    session = FakeSession()
    await persisti_transizioni_zona(session, {("z-a", "z-b"): ""}, "job-1")
    assert session.runs
    query, params = session.runs[0]
    assert "SUCCESSIONE_ZONA" in query
    assert params["riassunto"] == ""
