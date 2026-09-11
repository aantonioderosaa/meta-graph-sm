"""MT11 persistence of Livello 3 free relations (livello='3') + ciclo Quarantena."""

from __future__ import annotations

import pytest

from app.models.event_graph import (
    ArcoEvento,
    EventoRisolto,
    LivelloRelazioniResult,
    RelazioneLibera,
)
from app.pipeline.event_graph import livello_relazioni as livello_relazioni_mod
from app.pipeline.event_graph.livello_relazioni import (
    estrai_livello_relazioni,
    relazioni_causa_ciclo,
)
from app.pipeline.event_graph.persistence import persisti_livello_relazioni


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


def _queries(session: FakeSession) -> list[str]:
    return [query for query, _ in session.runs]


def _blob(session: FakeSession) -> str:
    return "\n".join(_queries(session))


def _assert_no_delete(session: FakeSession) -> None:
    for query, _ in session.runs:
        upper = query.upper()
        assert "DELETE" not in upper
        assert "DETACH" not in upper
        assert "REMOVE " not in upper


def _merge_shapes(session: FakeSession) -> list[tuple[str, dict[str, object]]]:
    keys = ("da_id", "a_id", "id")
    shapes: list[tuple[str, dict[str, object]]] = []
    for query, params in session.runs:
        identity = {key: params[key] for key in keys if key in params}
        shapes.append((query, identity))
    return shapes


def _eventi() -> list[EventoRisolto]:
    return [
        EventoRisolto(
            id="ev-1",
            lemma="arrivare",
            documento="doc-r",
            chunk_id="c0",
            span="arrivò",
        ),
        EventoRisolto(
            id="ev-2",
            lemma="partire",
            documento="doc-r",
            chunk_id="c1",
            span="partì",
        ),
    ]


def _payload() -> LivelloRelazioniResult:
    return LivelloRelazioniResult(
        relazioni=[
            RelazioneLibera(
                da_id="ev-1",
                a_id="ev-2",
                tipo="CAUSA",
                spiegazione="il primo provoca il secondo",
            ),
            RelazioneLibera(
                da_id="ev-2",
                a_id="ev-1",
                tipo="CONTRASTO",
                spiegazione="i due eventi divergono",
            ),
        ]
    )


def _rel_merges(session: FakeSession) -> list[tuple[str, dict]]:
    return [
        (query, params)
        for query, params in session.runs
        if "MERGE (da)-[r:" in query
    ]


@pytest.fixture(autouse=True)
def _reset_causa_ciclo():
    livello_relazioni_mod._dropped_causa_ciclo = []
    yield
    livello_relazioni_mod._dropped_causa_ciclo = []


@pytest.mark.asyncio
async def test_persist_causa_contrasto_livello_3_match_evento():
    session = FakeSession()
    await persisti_livello_relazioni(
        session, _payload(), "job-r", eventi=_eventi()
    )
    blob = _blob(session)
    assert "livello" in blob
    assert "'3'" in blob
    assert "spiegazione" in blob
    assert "CAUSA" in blob
    assert "CONTRASTO" in blob
    assert "MATCH (da:Evento" in blob
    assert "MATCH (a:Evento" in blob
    assert "MERGE (e:Evento" not in blob
    assert "MERGE (da:Evento" not in blob
    assert "MERGE (:Evento" not in blob
    assert any(params.get("spiegazione") for _, params in session.runs)
    _assert_no_delete(session)


@pytest.mark.asyncio
async def test_persist_twice_same_merge_shapes_no_delete():
    payload = _payload()
    eventi = _eventi()
    first = FakeSession()
    second = FakeSession()
    await persisti_livello_relazioni(first, payload, "job-r", eventi=eventi)
    await persisti_livello_relazioni(second, payload, "job-r", eventi=eventi)
    assert _merge_shapes(first) == _merge_shapes(second)
    assert _queries(first) == _queries(second)
    _assert_no_delete(first)
    _assert_no_delete(second)


@pytest.mark.asyncio
async def test_persist_none_writes_nothing():
    session = FakeSession()
    await persisti_livello_relazioni(session, None, "job-r")
    assert session.runs == []


@pytest.mark.asyncio
async def test_ciclo_causa_quarantena_not_merged_as_rel(monkeypatch):
    eventi = [
        EventoRisolto(
            id="e-0",
            lemma="arrivare",
            documento="doc-r",
            chunk_id="c0",
            span="arrivò",
        ),
        EventoRisolto(
            id="e-1",
            lemma="partire",
            documento="doc-r",
            chunk_id="c0",
            span="partì",
        ),
    ]

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return LivelloRelazioniResult(
            relazioni=[
                RelazioneLibera(
                    da_id="e-1",
                    a_id="e-0",
                    tipo="CAUSA",
                    spiegazione="chiuderebbe il ciclo",
                )
            ]
        )

    async def stub(
        system_prompt,
        user_prompt,
        response_model,
        temperature=0,
        job_id=None,
    ):
        return await handler(
            system_prompt, user_prompt, response_model, temperature, job_id
        )

    monkeypatch.setattr(
        "app.pipeline.event_graph.livello_relazioni.call_structured",
        stub,
    )
    result = await estrai_livello_relazioni(
        eventi,
        job_id="job-r",
        archi_causa_esistenti=[ArcoEvento(tipo="CAUSA", da_id="e-0", a_id="e-1")],
    )
    assert result is not None
    assert result.relazioni == []
    assert ("e-1", "e-0", "CAUSA") in relazioni_causa_ciclo()

    session = FakeSession()
    await persisti_livello_relazioni(session, result, "job-r", eventi=eventi)
    blob = _blob(session)
    assert ":Quarantena" in blob
    motivi = [str(params.get("motivo") or "") for _, params in session.runs]
    assert any("ciclo CAUSA livello 3" in motivo for motivo in motivi)
    dropped_rel = [
        (query, params)
        for query, params in _rel_merges(session)
        if params.get("da_id") == "e-1" and params.get("a_id") == "e-0"
    ]
    assert dropped_rel == []
    assert not any(
        "CAUSA" in query and "MERGE (da)-[r:" in query for query, _ in session.runs
    )
    _assert_no_delete(session)


@pytest.mark.asyncio
async def test_untrusted_tipo_not_interpolated_into_cypher():
    untrusted = "HACK_DROP"
    result = LivelloRelazioniResult.model_construct(
        relazioni=[
            RelazioneLibera(
                da_id="ev-1",
                a_id="ev-2",
                tipo="CAUSA",
                spiegazione="ok",
            ),
            RelazioneLibera.model_construct(
                da_id="ev-1",
                a_id="ev-2",
                tipo=untrusted,
                spiegazione="non deve comparire",
            ),
        ]
    )
    session = FakeSession()
    await persisti_livello_relazioni(session, result, "job-r", eventi=_eventi())
    blob = _blob(session)
    assert untrusted not in blob
    assert f"[r:{untrusted}" not in blob
    assert "CAUSA" in blob
    for query, _ in session.runs:
        assert untrusted not in query
    _assert_no_delete(session)
