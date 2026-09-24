"""MT7 persistence of ClusterTemporale / APPARTIENE_A / CONTEMPORANEO.

Da MT5 (`PIANO-LIVELLO-TEMPORALE-V2.md`) copre anche le proprietà nuove del
nodo, la foresta `CONTIENE` e l'`APPARTIENE_A` sulla sola foglia. Nessun Neo4j:
la `FakeSession` registra query e parametri, e le asserzioni guardano il testo
Cypher, non solo il numero di chiamate.
"""

from __future__ import annotations

import pytest

from app.models.event_graph import (
    ClusterTemporaleProposto,
    EventoRisolto,
    LivelloTemporaleResult,
    SegnaleTemporaleEvento,
)
from app.pipeline.event_graph import foresta_temporale, tempo_iso
from app.pipeline.event_graph.ids import cluster_temporale_id
from app.pipeline.event_graph.persistence import (
    EVENT_EVENT_TIPI,
    persisti_livello_temporale,
)
from app.pipeline.event_graph.wellformed import (
    EVENT_EVENT_TIPI as WELLFORMED_EVENT_EVENT_TIPI,
)

DOC = "doc-t"


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
    keys = ("id", "e_id", "c_id", "da", "a")
    shapes: list[tuple[str, dict[str, object]]] = []
    for query, params in session.runs:
        identity = {key: params[key] for key in keys if key in params}
        shapes.append((query, identity))
    return shapes


def _payload() -> LivelloTemporaleResult:
    return LivelloTemporaleResult(
        segnali=[
            SegnaleTemporaleEvento(
                evento_id="ev-1",
                tempo_assoluto="1994",
                contemporaneo_a=["ev-2"],
            ),
            SegnaleTemporaleEvento(
                evento_id="ev-2",
                contemporaneo_a=["ev-1"],
            ),
        ],
        cluster=[
            ClusterTemporaleProposto(
                etichetta="1994",
                tipo="data_esplicita",
                eventi=["ev-1", "ev-2"],
            ),
        ],
    )


def _eventi() -> list[EventoRisolto]:
    return [
        EventoRisolto(
            id="ev-1",
            lemma="arrivare",
            tempo_assoluto="1994",
            tempo_assoluto_revisioni=["1994"],
        ),
        EventoRisolto(id="ev-2", lemma="partire"),
    ]


@pytest.mark.asyncio
async def test_persist_cluster_appartiene_contemporaneo_tempo():
    session = FakeSession()
    await persisti_livello_temporale(
        session, _payload(), "doc-t", "job-t", eventi=_eventi()
    )
    blob = _blob(session)
    assert ":ClusterTemporale" in blob
    assert "APPARTIENE_A" in blob
    assert "CONTEMPORANEO" in blob
    assert "tempo_assoluto" in blob
    assert "MERGE (e:Fatto" not in blob
    assert blob.upper().count("CONTEMPORANEO") == 1
    _assert_no_delete(session)
    assert "CONTEMPORANEO" not in EVENT_EVENT_TIPI
    assert "APPARTIENE_A" not in EVENT_EVENT_TIPI
    assert "CONTEMPORANEO" not in WELLFORMED_EVENT_EVENT_TIPI
    assert "APPARTIENE_A" not in WELLFORMED_EVENT_EVENT_TIPI


@pytest.mark.asyncio
async def test_persist_twice_same_merge_shapes_no_delete():
    payload = _payload()
    eventi = _eventi()
    first = FakeSession()
    second = FakeSession()
    await persisti_livello_temporale(
        first, payload, "doc-t", "job-t", eventi=eventi
    )
    await persisti_livello_temporale(
        second, payload, "doc-t", "job-t", eventi=eventi
    )
    assert _merge_shapes(first) == _merge_shapes(second)
    assert _queries(first) == _queries(second)
    _assert_no_delete(first)
    _assert_no_delete(second)


@pytest.mark.asyncio
async def test_persist_none_writes_nothing():
    session = FakeSession()
    await persisti_livello_temporale(session, None, "doc-t", "job-t")
    assert session.runs == []


@pytest.mark.asyncio
async def test_invented_evento_id_does_not_merge_evento():
    session = FakeSession()
    result = LivelloTemporaleResult(
        cluster=[
            ClusterTemporaleProposto(
                etichetta="1994",
                tipo="data_esplicita",
                eventi=["ev-1", "ev-invented"],
            ),
        ],
    )
    await persisti_livello_temporale(
        session,
        result,
        "doc-t",
        "job-t",
        eventi=[EventoRisolto(id="ev-1", lemma="arrivare")],
    )
    blob = _blob(session)
    assert "MERGE (e:Fatto" not in blob
    assert "MERGE (:Fatto" not in blob
    assert ":ClusterTemporale" in blob
    assert "APPARTIENE_A" in blob
    invented_params = [
        params
        for _, params in session.runs
        if "ev-invented" in {params.get("e_id"), params.get("id"), params.get("da"), params.get("a")}
    ]
    assert invented_params == []
    evento_match = [
        (query, params)
        for query, params in session.runs
        if "MATCH (e:Fatto" in query
    ]
    assert evento_match
    assert all(params.get("e_id") == "ev-1" for _, params in evento_match)
    _assert_no_delete(session)


# --------------------------------------------------------------------------
# MT5 — proprietà nuove, foresta CONTIENE, APPARTIENE_A sulla foglia.
# --------------------------------------------------------------------------

MERGE_CLUSTER = "MERGE (c:ClusterTemporale {id: $id})"
MERGE_CONTIENE = "MERGE (p)-[r:CONTIENE]->(f)"
MERGE_APPARTIENE = "MERGE (e)-[r:APPARTIENE_A]->(c)"
SPEGNI_CONTIENE = "MATCH (p:ClusterTemporale)-[r:CONTIENE]->"
SPEGNI_APPARTIENE = "MATCH (e:Fatto {id: $e_id})-[r:APPARTIENE_A]->"

CYPHER_CONTEMPORANEO = (
    "MATCH (da:Fatto {id: $da}) "
    "MATCH (a:Fatto {id: $a}) "
    "MERGE (da)-[r:CONTEMPORANEO]->(a) "
    "SET r.regola = $regola, r.versione_regole = $versione_regole"
)


def _con(session: FakeSession, needle: str) -> list[dict[str, object]]:
    return [params for query, params in session.runs if needle in query]


def _cid(etichetta: str, tipo: str) -> str:
    return cluster_temporale_id(DOC, etichetta, tipo)


def _gerarchia() -> LivelloTemporaleResult:
    """Tre livelli: un contenitore puro, un mese, una sera.

    ``1843`` non ha eventi propri: è il caso che la versione precedente di
    ``persisti_livello_temporale`` saltava, azzerando i ``CONTIENE``.
    """
    return LivelloTemporaleResult(
        segnali=[
            SegnaleTemporaleEvento(
                evento_id="ev-1",
                tempo_assoluto="1843-12",
                confidenza=0.95,
                stimato=False,
            ),
            SegnaleTemporaleEvento(
                evento_id="ev-2",
                tempo_assoluto="1843-12-24T18",
                confidenza=0.5,
                stimato=True,
            ),
        ],
        cluster=[
            ClusterTemporaleProposto(
                etichetta="1843",
                tipo="intervallo",
                eventi=[],
                descrizione="L'anno intero del racconto",
                granularita="anno",
                inizio="1843",
                fine="1843-12-31",
                stimato=False,
                confidenza=0.9,
                padre=None,
            ),
            ClusterTemporaleProposto(
                etichetta="dicembre 1843",
                tipo="data_esplicita",
                eventi=["ev-1"],
                granularita="mese",
                inizio="1843-12",
                stimato=True,
                confidenza=0.8,
                padre="1843",
            ),
            ClusterTemporaleProposto(
                etichetta="24 dic, sera",
                tipo="data_esplicita",
                eventi=["ev-2"],
                granularita="ora",
                inizio="1843-12-24T18",
                stimato=False,
                confidenza=0.7,
                padre="dicembre 1843",
            ),
        ],
    )


def _eventi_gerarchia() -> list[EventoRisolto]:
    return [
        EventoRisolto(id="ev-1", lemma="arrivare", posizione_doc=5),
        EventoRisolto(id="ev-2", lemma="bussare", posizione_doc=2),
    ]


async def _persisti(
    payload: LivelloTemporaleResult | None,
    eventi: list[EventoRisolto] | None = None,
    session: FakeSession | None = None,
) -> FakeSession:
    session = session or FakeSession()
    await persisti_livello_temporale(session, payload, DOC, "job-t", eventi=eventi)
    return session


@pytest.mark.asyncio
async def test_nodo_porta_tutte_le_proprieta_nuove():
    session = await _persisti(_gerarchia(), _eventi_gerarchia())
    nodi = {params["etichetta"]: params for params in _con(session, MERGE_CLUSTER)}
    assert set(nodi) == {"1843", "dicembre 1843", "24 dic, sera"}

    anno = nodi["1843"]
    assert anno["id"] == _cid("1843", "intervallo")
    assert anno["documento"] == DOC
    assert anno["tipo"] == "intervallo"
    assert anno["descrizione"] == "L'anno intero del racconto"
    assert anno["granularita"] == "anno"
    assert anno["inizio"] == "1843"
    assert anno["fine"] == "1843-12-31"
    assert anno["stimato"] is False
    assert anno["confidenza"] == pytest.approx(0.9)
    assert anno["chiave_ordine"] == tempo_iso.chiave_ordine("1843", "anno")

    sera = nodi["24 dic, sera"]
    assert sera["granularita"] == "ora"
    assert sera["stimato"] is False
    assert sera["confidenza"] == pytest.approx(0.7)
    assert nodi["dicembre 1843"]["stimato"] is True
    assert nodi["dicembre 1843"]["fine"] is None
    assert nodi["dicembre 1843"]["descrizione"] is None

    blob = _blob(session)
    for prop in (
        "c.descrizione",
        "c.granularita",
        "c.inizio",
        "c.fine",
        "c.chiave_ordine",
        "c.stimato",
        "c.confidenza",
    ):
        assert prop in blob
    _assert_no_delete(session)


@pytest.mark.asyncio
async def test_contenitore_puro_viene_scritto():
    """Un cluster con ``eventi == []`` è un nodo, non un cluster da saltare."""
    session = await _persisti(_gerarchia(), _eventi_gerarchia())
    scritti = [params["etichetta"] for params in _con(session, MERGE_CLUSTER)]
    assert "1843" in scritti
    assert _con(session, MERGE_CONTIENE)


@pytest.mark.asyncio
async def test_contiene_copre_tutti_gli_archi_della_foresta():
    payload = _gerarchia()
    session = await _persisti(payload, _eventi_gerarchia())
    tipi = {
        (cluster.etichetta or "").strip(): str(cluster.tipo)
        for cluster in payload.cluster
    }
    attesi = {
        (_cid(padre, tipi[padre]), _cid(figlio, tipi[figlio]))
        for padre, figlio in foresta_temporale.archi_contiene(payload.cluster)
    }
    assert attesi
    scritti = {
        (params["p_id"], params["f_id"]) for params in _con(session, MERGE_CONTIENE)
    }
    assert scritti == attesi


@pytest.mark.asyncio
async def test_contiene_scritto_dopo_i_nodi():
    """Gli archi sono ``MATCH``-based: entrambi i nodi devono già esistere."""
    session = await _persisti(_gerarchia(), _eventi_gerarchia())
    queries = _queries(session)
    ultimo_nodo = max(i for i, q in enumerate(queries) if MERGE_CLUSTER in q)
    primo_arco = min(i for i, q in enumerate(queries) if MERGE_CONTIENE in q)
    assert ultimo_nodo < primo_arco


@pytest.mark.asyncio
async def test_appartiene_a_solo_alla_foglia():
    """L'evento nell'antenato e nella foglia produce un arco solo, alla foglia."""
    payload = _gerarchia()
    payload.cluster[0].eventi = ["ev-2"]
    payload.cluster[1].eventi = ["ev-1", "ev-2"]
    session = await _persisti(payload, _eventi_gerarchia())
    archi = [
        (params["e_id"], params["c_id"]) for params in _con(session, MERGE_APPARTIENE)
    ]
    assert [eid for eid, _ in archi].count("ev-2") == 1
    assert ("ev-2", _cid("24 dic, sera", "data_esplicita")) in archi
    assert ("ev-1", _cid("dicembre 1843", "data_esplicita")) in archi
    assert _cid("1843", "intervallo") not in {cid for _, cid in archi}


@pytest.mark.asyncio
async def test_appartiene_a_porta_confidenza_e_stimato():
    """Congiunzione fra raggruppamento (cluster) e collocazione (segnale)."""
    session = await _persisti(_gerarchia(), _eventi_gerarchia())
    archi = {params["e_id"]: params for params in _con(session, MERGE_APPARTIENE)}
    # cluster 0.8 / stimato True, segnale 0.95 / stimato False.
    assert archi["ev-1"]["confidenza"] == pytest.approx(0.8)
    assert archi["ev-1"]["stimato"] is True
    # cluster 0.7 / stimato False, segnale 0.5 / stimato True.
    assert archi["ev-2"]["confidenza"] == pytest.approx(0.5)
    assert archi["ev-2"]["stimato"] is True
    assert "r.confidenza = $confidenza" in _blob(session)
    assert "r.stimato = $stimato" in _blob(session)


@pytest.mark.asyncio
async def test_appartiene_a_senza_segnale_ricade_sul_cluster():
    payload = _gerarchia()
    payload.segnali = []
    session = await _persisti(payload, _eventi_gerarchia())
    archi = {params["e_id"]: params for params in _con(session, MERGE_APPARTIENE)}
    assert archi["ev-1"]["confidenza"] == pytest.approx(0.8)
    assert archi["ev-1"]["stimato"] is True
    assert archi["ev-2"]["confidenza"] == pytest.approx(0.7)
    assert archi["ev-2"]["stimato"] is False


@pytest.mark.asyncio
async def test_chiave_ordine_coerente_e_monotona():
    payload = _gerarchia()
    session = await _persisti(payload, _eventi_gerarchia())
    nodi = {params["etichetta"]: params for params in _con(session, MERGE_CLUSTER)}
    per_etichetta = {
        (cluster.etichetta or "").strip(): cluster for cluster in payload.cluster
    }
    for etichetta, params in nodi.items():
        cluster = per_etichetta[etichetta]
        assert params["chiave_ordine"] == tempo_iso.chiave_ordine(
            cluster.inizio, cluster.granularita
        )
    per_inizio = sorted(nodi, key=lambda e: per_etichetta[e].inizio or "")
    per_chiave = sorted(nodi, key=lambda e: nodi[e]["chiave_ordine"])
    assert per_chiave == per_inizio == ["1843", "dicembre 1843", "24 dic, sera"]


@pytest.mark.asyncio
async def test_chiave_ordine_nulla_senza_collocazione_e_fallback_separato():
    """Nessun surrogato sulla scala sbagliata: il fallback è un campo suo.

    ``chiave_ordine`` è in sedicesimi di secondo dall'anno 1 (~9e11),
    ``posizione_doc_min`` è un ordinale piccolo: infilare il secondo nel primo
    metterebbe ogni cluster non datato all'estrema sinistra dell'asse.
    """
    payload = LivelloTemporaleResult(
        cluster=[
            ClusterTemporaleProposto(
                etichetta="sette anni prima",
                tipo="relativo",
                eventi=["ev-1", "ev-2"],
            ),
        ],
    )
    session = await _persisti(payload, _eventi_gerarchia())
    (nodo,) = _con(session, MERGE_CLUSTER)
    assert nodo["chiave_ordine"] is None
    assert nodo["inizio"] is None
    assert nodo["posizione_doc_min"] == 2


@pytest.mark.asyncio
async def test_posizione_doc_min_risale_ai_contenitori():
    """Il contenitore puro eredita la posizione minima del sottoalbero."""
    session = await _persisti(_gerarchia(), _eventi_gerarchia())
    nodi = {params["etichetta"]: params for params in _con(session, MERGE_CLUSTER)}
    assert nodi["24 dic, sera"]["posizione_doc_min"] == 2
    assert nodi["dicembre 1843"]["posizione_doc_min"] == 2
    assert nodi["1843"]["posizione_doc_min"] == 2


@pytest.mark.asyncio
async def test_contemporaneo_invariato():
    """Non-regressione: il piano vieta di toccare i CONTEMPORANEO in MT5."""
    session = await _persisti(_payload(), _eventi())
    contemporanei = [
        (query, params)
        for query, params in session.runs
        if "CONTEMPORANEO" in query
    ]
    assert len(contemporanei) == 1
    query, params = contemporanei[0]
    assert query == CYPHER_CONTEMPORANEO
    assert params["da"] == "ev-1"
    assert params["a"] == "ev-2"
    for altra, _ in session.runs:
        if "CONTEMPORANEO" in altra:
            continue
        assert "CONTEMPORANEO" not in altra


@pytest.mark.asyncio
async def test_none_e_cluster_vuoti_non_scrivono_nulla():
    assert (await _persisti(None)).runs == []
    assert (await _persisti(LivelloTemporaleResult())).runs == []
    vuoto = LivelloTemporaleResult(
        cluster=[ClusterTemporaleProposto(etichetta="   ", tipo="simbolico")]
    )
    assert (await _persisti(vuoto)).runs == []


@pytest.mark.asyncio
async def test_ri_ingestione_spegne_il_padre_superato():
    """≤ 1 padre senza cancellare: l'arco vecchio resta con ``attivo = false``."""
    session = await _persisti(_gerarchia(), _eventi_gerarchia())
    spegnimenti = {params["f_id"]: params for params in _con(session, SPEGNI_CONTIENE)}
    assert set(spegnimenti) == {
        _cid("1843", "intervallo"),
        _cid("dicembre 1843", "data_esplicita"),
        _cid("24 dic, sera", "data_esplicita"),
    }
    # La radice non ha padre: ogni CONTIENE entrante è superato.
    assert spegnimenti[_cid("1843", "intervallo")]["p_id"] is None
    assert spegnimenti[_cid("dicembre 1843", "data_esplicita")]["p_id"] == _cid(
        "1843", "intervallo"
    )
    blob = _blob(session)
    assert "r.attivo = false" in blob
    assert "r.attivo = true" in blob
    assert "WHERE $p_id IS NULL OR p.id <> $p_id" in blob
    _assert_no_delete(session)

    foglie = {params["e_id"]: params for params in _con(session, SPEGNI_APPARTIENE)}
    assert foglie["ev-1"]["c_id"] == _cid("dicembre 1843", "data_esplicita")
    assert foglie["ev-2"]["c_id"] == _cid("24 dic, sera", "data_esplicita")


@pytest.mark.asyncio
async def test_gerarchia_idempotente():
    prima = await _persisti(_gerarchia(), _eventi_gerarchia())
    dopo = await _persisti(_gerarchia(), _eventi_gerarchia())
    assert prima.runs == dopo.runs


class SessioneRotta(FakeSession):
    """Fallisce su una query sola: il resto della scrittura deve continuare."""

    def __init__(self, needle: str, valore: str | None = None):
        super().__init__()
        self.needle = needle
        self.valore = valore

    async def run(self, query, parameters=None, **params):
        merged = {**(parameters or {}), **params}
        if self.needle in query and (
            self.valore is None or self.valore in merged.values()
        ):
            raise RuntimeError("boom")
        return await super().run(query, parameters, **params)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "needle",
    [
        MERGE_CLUSTER,
        MERGE_CONTIENE,
        MERGE_APPARTIENE,
        SPEGNI_CONTIENE,
        SPEGNI_APPARTIENE,
    ],
)
async def test_fallimento_parziale_non_ferma_il_resto(needle: str):
    session = SessioneRotta(needle)
    await persisti_livello_temporale(
        session, _gerarchia(), DOC, "job-t", eventi=_eventi_gerarchia()
    )
    scritte = [query for query in _queries(session) if needle not in query]
    assert scritte
    assert any("ClusterTemporale" in query for query in scritte)
    # Le proprietà temporali degli eventi si scrivono comunque.
    assert _con(session, "e.tempo_assoluto = $tempo_assoluto")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rotto", "atteso"),
    [
        (SPEGNI_CONTIENE, MERGE_CONTIENE),
        (SPEGNI_APPARTIENE, MERGE_APPARTIENE),
    ],
)
async def test_tombstone_rotta_non_annulla_l_arco(rotto: str, atteso: str):
    """La tombstone è una pulizia: se fallisce, l'arco vero si scrive lo stesso."""
    session = SessioneRotta(rotto)
    await persisti_livello_temporale(
        session, _gerarchia(), DOC, "job-t", eventi=_eventi_gerarchia()
    )
    assert len(_con(session, atteso)) == 2


@pytest.mark.asyncio
async def test_fallimento_su_un_cluster_non_blocca_gli_altri():
    """Un cluster che esplode non porta con sé gli altri due."""
    rotto = _cid("dicembre 1843", "data_esplicita")
    session = SessioneRotta(MERGE_CLUSTER, rotto)
    await persisti_livello_temporale(
        session, _gerarchia(), DOC, "job-t", eventi=_eventi_gerarchia()
    )
    scritti = {params["etichetta"] for params in _con(session, MERGE_CLUSTER)}
    assert scritti == {"1843", "24 dic, sera"}
    assert len(_con(session, MERGE_CONTIENE)) == 2
    assert len(_con(session, MERGE_APPARTIENE)) == 2
    assert _con(session, "e.tempo_assoluto = $tempo_assoluto")


@pytest.mark.asyncio
async def test_precede_e_contemporaneo_non_si_scrivono_sulla_stessa_coppia():
    payload = LivelloTemporaleResult(
        segnali=[
            SegnaleTemporaleEvento(
                evento_id="ev-1",
                tempo_assoluto="1994",
                contemporaneo_a=["ev-2"],
                precede=["ev-2"],
            ),
            SegnaleTemporaleEvento(evento_id="ev-2"),
        ]
    )
    session = FakeSession()
    await persisti_livello_temporale(
        session, payload, DOC, "job-t", eventi=_eventi()
    )
    blob = _blob(session)
    assert "MERGE (da)-[r:PRECEDE]->(a)" in blob
    assert "CONTEMPORANEO" not in blob


@pytest.mark.asyncio
async def test_contemporaneo_saltato_se_la_coppia_ha_gia_un_precede():
    session = FakeSession()
    await persisti_livello_temporale(
        session,
        _payload(),
        DOC,
        "job-t",
        eventi=_eventi(),
        coppie_precede={frozenset(("ev-1", "ev-2"))},
    )
    assert "CONTEMPORANEO" not in _blob(session)
