"""MT7 persistence of AncoraTemporale / CONTIENE / APPARTIENE_A / SUCCESSIONE_ANCORA.

No Neo4j: FakeSession records Cypher text and params. MERGE of every
relationship must carry ``id`` in the pattern (the defect of
``persisti_livello_temporale``).
"""

from __future__ import annotations

import pytest

from app.models.event_graph import (
    NATURA_ANCORE,
    TIPI_ANCORA,
    AncoraTemporaleProposta,
    EventoRisolto,
)
from app.pipeline.event_graph.ancore_identita import identita_ancora
from app.pipeline.event_graph.ancore_linea import LineaAncore
from app.pipeline.event_graph.ancore_smistamento import (
    AppartenenzaAncora,
    SmistamentoAncore,
)
from app.pipeline.event_graph.ids import content_hash
from app.pipeline.event_graph.persistence import persisti_livello_ancore

DOC = "doc-a"

MERGE_ANCORA = "MERGE (a:AncoraTemporale {id: $id})"
MERGE_CONTIENE = "MERGE (p)-[r:CONTIENE {id: $rel_id}]->(f)"
MERGE_APPARTIENE = "MERGE (e)-[r:APPARTIENE_A {id: $rel_id}]->(a)"
MERGE_SUCCESSIONE = "MERGE (da)-[r:SUCCESSIONE_ANCORA {id: $rel_id}]->(a)"
MERGE_PRECEDE_CON_ID = "MERGE (da)-[r:PRECEDE {id: $rel_id}]->(a)"
MERGE_CONTEMPORANEO_CON_ID = "MERGE (da)-[r:CONTEMPORANEO {id: $rel_id}]->(a)"
PRECEDE_SENZA_ID = "MERGE (da)-[r:PRECEDE]->(a)"
CONTEMPORANEO_SENZA_ID = "MERGE (da)-[r:CONTEMPORANEO]->(a)"


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
    keys = ("id", "e_id", "a_id", "p_id", "f_id", "da_id", "rel_id", "da")
    shapes: list[tuple[str, dict[str, object]]] = []
    for query, params in session.runs:
        identity = {key: params[key] for key in keys if key in params}
        shapes.append((query, identity))
    return shapes


def _con(session: FakeSession, needle: str) -> list[dict[str, object]]:
    return [params for query, params in session.runs if needle in query]


def _ancora(**kwargs) -> AncoraTemporaleProposta:
    kwargs.setdefault("natura", "esplicita")
    kwargs.setdefault("tipo", "data")
    return AncoraTemporaleProposta(**kwargs)


def _app(
    eid: str,
    foglia: str,
    *,
    confidenza: float = 1.0,
    stimato: bool = False,
    base: str | None = "bounds",
) -> AppartenenzaAncora:
    return AppartenenzaAncora(eid, foglia, confidenza, stimato, base)


def _payload() -> SmistamentoAncore:
    anno = _ancora(
        etichetta="1843",
        inizio="1843",
        granularita="anno",
        descrizione="L'anno del racconto",
        posizione_doc_min=2,
        padre=None,
    )
    giorno = _ancora(
        etichetta="24 dic",
        inizio="1843-12-24",
        granularita="giorno",
        padre="1843",
        posizione_doc_min=2,
        offset_inizio=10,
        offset_fine=16,
        espressione="24 dicembre",
    )
    intervallo = _ancora(
        etichetta="fra 24 e 25",
        natura="intervallo",
        tipo="data",
        padre="1843",
        posizione_doc_min=8,
        stimato=True,
        confidenza=0.6,
    )
    return SmistamentoAncore(
        linea=LineaAncore(
            ancore=[anno, giorno, intervallo],
            chiave_ordine={"1843": 111, "24 dic": 222},
            ordinale={"1843": 0, "24 dic": 0, "fra 24 e 25": 1},
            successione=[("24 dic", "fra 24 e 25")],
        ),
        appartenenze=[
            _app("ev-1", "1843", confidenza=0.9),
            _app("ev-1", "24 dic", confidenza=0.8, stimato=False),
            _app("ev-2", "fra 24 e 25", confidenza=0.5, stimato=True, base="llm"),
        ],
    )


def _eventi() -> list[EventoRisolto]:
    return [
        EventoRisolto(id="ev-1", lemma="arrivare", posizione_doc=2),
        EventoRisolto(id="ev-2", lemma="partire", posizione_doc=8),
        EventoRisolto(id="ev-3", lemma="restare", posizione_doc=3),
    ]


def _nid(etichetta: str, payload: SmistamentoAncore | None = None) -> str:
    payload = payload or _payload()
    for ancora in payload.linea.ancore:
        if ancora.etichetta == etichetta:
            return identita_ancora(ancora, DOC)
    raise AssertionError(etichetta)


async def _persisti(
    payload: SmistamentoAncore | None,
    eventi: list[EventoRisolto] | None = None,
    session: FakeSession | None = None,
) -> FakeSession:
    session = session or FakeSession()
    await persisti_livello_ancore(session, payload, DOC, "job-a", eventi=eventi)
    return session


def _assert_merge_rel_ha_id(query: str) -> None:
    merge = query[query.index("MERGE") :] if "MERGE" in query else query
    assert "{id: $rel_id}" in merge or "id: $rel_id" in merge or "id: $" in merge


@pytest.mark.asyncio
async def test_merge_ancora_temporale_con_id():
    session = await _persisti(_payload(), _eventi())
    blob = _blob(session)
    assert MERGE_ANCORA in blob
    nodi = {params["etichetta"]: params for params in _con(session, MERGE_ANCORA)}
    assert set(nodi) == {"1843", "24 dic", "fra 24 e 25"}
    anno = nodi["1843"]
    assert anno["id"] == _nid("1843")
    assert anno["documento"] == DOC
    assert anno["natura"] == "esplicita"
    assert anno["tipo"] == "data"
    assert anno["descrizione"] == "L'anno del racconto"
    assert anno["granularita"] == "anno"
    assert anno["inizio"] == "1843"
    assert anno["chiave_ordine"] == 111
    assert anno["ordinale"] == 0
    assert anno["posizione_doc_min"] == 2
    giorno = nodi["24 dic"]
    assert giorno["espressione"] == "24 dicembre"
    assert giorno["offset_inizio"] == 10
    assert giorno["offset_fine"] == 16
    assert giorno["chiave_ordine"] == 222
    assert giorno["ordinale"] == 0
    gap = nodi["fra 24 e 25"]
    assert gap["natura"] == "intervallo"
    assert gap["chiave_ordine"] is None
    assert gap["ordinale"] == 1
    assert gap["stimato"] is True
    assert gap["confidenza"] == pytest.approx(0.6)
    _assert_no_delete(session)


@pytest.mark.asyncio
async def test_tutti_gli_archi_merge_con_id():
    session = await _persisti(_payload(), _eventi())
    blob = _blob(session)
    assert MERGE_CONTIENE in blob
    assert MERGE_APPARTIENE in blob
    assert MERGE_SUCCESSIONE in blob
    assert MERGE_PRECEDE_CON_ID not in blob
    assert MERGE_CONTEMPORANEO_CON_ID not in blob
    assert PRECEDE_SENZA_ID not in blob
    assert CONTEMPORANEO_SENZA_ID not in blob
    for needle in (
        MERGE_CONTIENE,
        MERGE_APPARTIENE,
        MERGE_SUCCESSIONE,
    ):
        for query, params in session.runs:
            if needle not in query:
                continue
            _assert_merge_rel_ha_id(query)
            assert params.get("rel_id")
    contiene = {(p["p_id"], p["f_id"]) for p in _con(session, MERGE_CONTIENE)}
    assert contiene == {
        (_nid("1843"), _nid("24 dic")),
        (_nid("1843"), _nid("fra 24 e 25")),
    }
    for params in _con(session, MERGE_CONTIENE):
        assert params["rel_id"] == content_hash(
            f"CONTIENE|{params['p_id']}|{params['f_id']}|{DOC}"
        )
    succ = _con(session, MERGE_SUCCESSIONE)
    assert len(succ) == 1
    assert succ[0]["da_id"] == _nid("24 dic")
    assert succ[0]["a_id"] == _nid("fra 24 e 25")
    assert succ[0]["rel_id"] == content_hash(
        f"SUCCESSIONE_ANCORA|{succ[0]['da_id']}|{succ[0]['a_id']}|{DOC}"
    )
    _assert_no_delete(session)


@pytest.mark.asyncio
async def test_appartiene_a_solo_alla_foglia():
    session = await _persisti(_payload(), _eventi())
    archi = [
        (params["e_id"], params["a_id"]) for params in _con(session, MERGE_APPARTIENE)
    ]
    assert [eid for eid, _ in archi].count("ev-1") == 1
    assert ("ev-1", _nid("24 dic")) in archi
    assert ("ev-2", _nid("fra 24 e 25")) in archi
    assert _nid("1843") not in {aid for _, aid in archi}
    by_e = {params["e_id"]: params for params in _con(session, MERGE_APPARTIENE)}
    assert by_e["ev-1"]["confidenza"] == pytest.approx(0.8)
    assert by_e["ev-1"]["stimato"] is False
    assert by_e["ev-2"]["stimato"] is True
    assert by_e["ev-1"]["rel_id"] == content_hash(
        f"APPARTIENE_A|ev-1|{_nid('24 dic')}|{DOC}"
    )


@pytest.mark.asyncio
async def test_due_passate_stessi_merge_keys():
    payload = _payload()
    eventi = _eventi()
    first = await _persisti(payload, eventi)
    second = await _persisti(payload, eventi)
    assert _merge_shapes(first) == _merge_shapes(second)
    assert _queries(first) == _queries(second)
    _assert_no_delete(first)


@pytest.mark.asyncio
async def test_none_e_vuoto_zero_runs():
    assert (await _persisti(None)).runs == []
    vuoto = SmistamentoAncore()
    assert (await _persisti(vuoto)).runs == []


@pytest.mark.asyncio
async def test_precede_e_contemporaneo_non_si_scrivono():
    session = await _persisti(_payload(), _eventi())
    blob = _blob(session)
    assert PRECEDE_SENZA_ID not in blob
    assert CONTEMPORANEO_SENZA_ID not in blob
    assert MERGE_PRECEDE_CON_ID not in blob
    assert MERGE_CONTEMPORANEO_CON_ID not in blob
    assert not any(
        "PRECEDE" in query or "CONTEMPORANEO" in query for query, _ in session.runs
    )


@pytest.mark.asyncio
async def test_nodi_prima_degli_archi_match():
    session = await _persisti(_payload(), _eventi())
    queries = _queries(session)
    ultimo_nodo = max(i for i, q in enumerate(queries) if MERGE_ANCORA in q)
    primi = [
        i
        for i, q in enumerate(queries)
        if MERGE_CONTIENE in q or MERGE_APPARTIENE in q or MERGE_SUCCESSIONE in q
    ]
    assert primi
    assert ultimo_nodo < min(primi)


@pytest.mark.asyncio
async def test_invented_evento_id_non_scrive_appartiene():
    payload = _payload()
    payload.appartenenze.append(
        _app("ev-invented", "24 dic")
    )
    session = await _persisti(payload, _eventi())
    blob = _blob(session)
    assert "MERGE (e:Evento" not in blob
    assert "MERGE (:Evento" not in blob
    e_ids = {params.get("e_id") for params in _con(session, MERGE_APPARTIENE)}
    assert "ev-invented" not in e_ids
    assert not any(
        "PRECEDE" in query or "CONTEMPORANEO" in query for query, _ in session.runs
    )


@pytest.mark.asyncio
async def test_etichetta_persistita_non_e_natura_ne_tipo():
    vietate = set(NATURA_ANCORE) | set(TIPI_ANCORA)
    session = await _persisti(_payload(), _eventi())
    etichette = [params["etichetta"] for params in _con(session, MERGE_ANCORA)]
    assert etichette
    assert all(etichetta not in vietate for etichetta in etichette)
