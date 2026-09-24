"""MT6: vista `grafo_livello2` — gerarchia, SUCCESSIONE_ANCORA, ordinamento a bande.

Nessun Neo4j, nessuna ingestione, nessun LLM. La FakeSession è quella di m19
(chiave = sottostringa della query: i commenti `/* grafo_livello2_* */` non
si rinominali). Intra-cluster = stesso parent foglia (`APPARTIENE_A`), non LCA.
Drawn edges are SUCCESSIONE_ANCORA only; no PRECEDE / CONTEMPORANEO.
"""

from __future__ import annotations

from app.pipeline.event_graph.catalog import (
    _L2_CLUSTER_CYPHER,
    _L2_EVENTS_CYPHER,
    _L2_SUCCESSIONE_CYPHER,
    grafo,
    grafo_livello1,
    grafo_livello2,
    grafo_livello3,
)
from tests.test_event_graph_m19 import (
    FakeSession,
    _assert_livello1_shape,
    _assert_livello2_shape,
    _assert_livello3_shape,
    _livello1_session,
    _livello2_session,
    _livello3_session,
)

_PROPS_ANCORA = (
    "chiave_ordine",
    "granularita",
    "stimato",
    "confidenza",
    "etichetta",
    "descrizione",
    "inizio",
    "fine",
    "posizione_doc_min",
    "ordine_vista",
    "natura",
    "ordinale",
)


def _l2_session(
    *,
    cluster: list[dict] | None = None,
    eventi: list[dict] | None = None,
    successione: list[dict] | None = None,
) -> FakeSession:
    return FakeSession(
        mapping={
            "grafo_livello2_cluster": list(cluster or []),
            "grafo_livello2_eventi": list(eventi or []),
            "grafo_livello2_successione_ancora": list(successione or []),
        }
    )


def _by_id(body: dict) -> dict[str, dict]:
    return {node["data"]["id"]: node["data"] for node in body["elements"]["nodes"]}


def _cluster_ids_in_order(body: dict) -> list[str]:
    return [
        node["data"]["id"]
        for node in body["elements"]["nodes"]
        if node["data"].get("tipo") == "AncoraTemporale"
    ]


def _edge_ids(body: dict, tipo: str | None = None) -> set[str]:
    edges = body["elements"]["edges"]
    if tipo is None:
        return {edge["data"]["id"] for edge in edges}
    return {edge["data"]["id"] for edge in edges if edge["data"].get("tipo") == tipo}


def _cluster_row(
    cid: str,
    *,
    parent: str | None = None,
    etichetta: str | None = None,
    descrizione: str | None = None,
    granularita: str | None = None,
    inizio: str | None = None,
    fine: str | None = None,
    chiave_ordine: int | None = None,
    posizione_doc_min: int | None = None,
    stimato: bool | None = None,
    confidenza: float | None = None,
    tipo_cluster: str | None = "intervallo",
    natura: str | None = "esplicita",
    ordinale: int | None = None,
) -> dict:
    label = etichetta if etichetta is not None else cid
    return {
        "id": cid,
        "label": label,
        "etichetta": etichetta if etichetta is not None else cid,
        "tipo_cluster": tipo_cluster,
        "documento": "doc-1",
        "tipo": "AncoraTemporale",
        "descrizione": descrizione,
        "granularita": granularita,
        "inizio": inizio,
        "fine": fine,
        "chiave_ordine": chiave_ordine,
        "posizione_doc_min": posizione_doc_min,
        "stimato": stimato,
        "confidenza": confidenza,
        "natura": natura,
        "ordinale": ordinale,
        "parent": parent,
    }


def _evento_row(
    eid: str,
    *,
    parent: str | None = None,
    posizione_doc: int | None = None,
    posizione_chunk: int | None = None,
    offset_inizio: int | None = None,
    stimato: bool | None = None,
    confidenza: float | None = None,
    label: str | None = None,
) -> dict:
    row: dict = {
        "id": eid,
        "label": label or eid,
        "parent": parent,
        "documento": "doc-1",
        "tipo": "Fatto",
        "posizione_doc": posizione_doc,
        "posizione_chunk": posizione_chunk,
        "offset_inizio": offset_inizio,
        "stimato": stimato,
        "confidenza": confidenza,
    }
    return row


def _edge_row(eid: str, source: str, target: str, tipo: str, **extra: object) -> dict:
    row = {"id": eid, "source": source, "target": target, "tipo": tipo}
    row.update(extra)
    return row


async def test_cluster_espone_proprieta_nuove():
    session = _l2_session(
        cluster=[
            _cluster_row(
                "cl-sera",
                etichetta="24 dic, sera",
                descrizione="La vigilia, dopo il tramonto",
                granularita="ora",
                inizio="1843-12-24T18",
                fine="1843-12-24T22",
                chiave_ordine=100,
                posizione_doc_min=3,
                stimato=True,
                confidenza=0.81,
                tipo_cluster="ora",
                natura="esplicita",
                ordinale=2,
            )
        ],
        eventi=[_evento_row("ev-1", parent="cl-sera", posizione_doc=3)],
    )
    body = await grafo_livello2(session, documento="doc-1")
    cluster = _by_id(body)["cl-sera"]
    for key in _PROPS_ANCORA:
        assert key in cluster, key
    assert cluster["etichetta"] == "24 dic, sera"
    assert cluster["label"] == "24 dic, sera"
    assert cluster["descrizione"] == "La vigilia, dopo il tramonto"
    assert cluster["granularita"] == "ora"
    assert cluster["inizio"] == "1843-12-24T18"
    assert cluster["fine"] == "1843-12-24T22"
    assert cluster["chiave_ordine"] == 100
    assert cluster["posizione_doc_min"] == 3
    assert cluster["stimato"] is True
    assert cluster["confidenza"] == 0.81
    assert cluster["natura"] == "esplicita"
    assert cluster["ordinale"] == 2
    assert cluster["ordine_vista"] == 0
    assert cluster["tipo"] == "AncoraTemporale"


async def test_gerarchia_due_livelli_parent_su_figlio_e_foglia():
    """Nonno senza parent, figlio.parent = nonno, evento.parent = foglia.

    CONTIENE e APPARTIENE_A non sono archi disegnati: assegnano `data.parent`.
    """
    session = _l2_session(
        cluster=[
            _cluster_row(
                "cl-1843",
                etichetta="1843",
                granularita="anno",
                inizio="1843",
                chiave_ordine=50,
            ),
            _cluster_row(
                "cl-dic",
                parent="cl-1843",
                etichetta="dicembre",
                granularita="mese",
                inizio="1843-12",
                chiave_ordine=60,
            ),
        ],
        eventi=[_evento_row("ev-a", parent="cl-dic", posizione_doc=2)],
    )
    body = await grafo_livello2(session)
    by_id = _by_id(body)
    assert "parent" not in by_id["cl-1843"]
    assert by_id["cl-dic"]["parent"] == "cl-1843"
    assert by_id["ev-a"]["parent"] == "cl-dic"
    tipi_archi = {edge["data"]["tipo"] for edge in body["elements"]["edges"]}
    assert tipi_archi.isdisjoint({"CONTIENE", "APPARTIENE_A"})
    cluster_order = _cluster_ids_in_order(body)
    assert cluster_order[0] == "cl-1843"
    assert cluster_order[1] == "cl-dic"
    node_ids = [node["data"]["id"] for node in body["elements"]["nodes"]]
    assert node_ids.index("cl-dic") < node_ids.index("ev-a")


async def test_parent_assente_fra_i_nodi_viene_rimosso():
    session = _l2_session(
        cluster=[
            _cluster_row("cl-vivo", parent="cl-fantasma", chiave_ordine=10),
        ],
        eventi=[
            _evento_row(
                "ev-orfano",
                parent="cl-fantasma",
                posizione_doc=1,
                stimato=True,
                confidenza=0.9,
            ),
        ],
    )
    body = await grafo_livello2(session)
    by_id = _by_id(body)
    assert "cl-fantasma" not in by_id
    assert "parent" not in by_id["cl-vivo"]
    assert "parent" not in by_id["ev-orfano"]
    assert "stimato" not in by_id["ev-orfano"]
    assert "confidenza" not in by_id["ev-orfano"]


async def test_archi_inattivi_ignorati_cypher_e_vista():
    """FakeSession simula il filtro Cypher: parent=None se CONTIENE/APPARTIENE_A
    ha `attivo=false`. Il Cypher deve comunque contenere `coalesce(..., true)`.
    """
    assert "coalesce(rc.attivo, true)" in _L2_CLUSTER_CYPHER
    assert "coalesce(ra.attivo, true)" in _L2_EVENTS_CYPHER
    assert "e.offset_inizio AS offset_inizio" in _L2_EVENTS_CYPHER
    assert "e.posizione_chunk AS posizione_chunk" in _L2_EVENTS_CYPHER
    assert "coalesce(r.attivo, true)" in _L2_SUCCESSIONE_CYPHER
    session = _l2_session(
        cluster=[
            _cluster_row("cl-padre", chiave_ordine=10),
            # CONTIENE inattivo: Cypher non restituisce p.id.
            _cluster_row("cl-ex-figlio", parent=None, chiave_ordine=20),
            _cluster_row("cl-figlio", parent="cl-padre", chiave_ordine=15),
        ],
        eventi=[
            # APPARTIENE_A inattivo: Cypher non restituisce c.id.
            _evento_row("ev-staccato", parent=None, posizione_doc=1),
            _evento_row(
                "ev-attivo",
                parent="cl-figlio",
                posizione_doc=2,
                stimato=False,
                confidenza=0.7,
            ),
        ],
    )
    body = await grafo_livello2(session)
    by_id = _by_id(body)
    assert "parent" not in by_id["cl-ex-figlio"]
    assert by_id["cl-figlio"]["parent"] == "cl-padre"
    assert "parent" not in by_id["ev-staccato"]
    assert by_id["ev-attivo"]["parent"] == "cl-figlio"
    assert by_id["ev-attivo"]["stimato"] is False
    assert by_id["ev-attivo"]["confidenza"] == 0.7
    blob = " ".join(query for query, _ in session.runs)
    assert "coalesce(rc.attivo, true)" in blob
    assert "coalesce(ra.attivo, true)" in blob


async def test_l2_disegna_solo_successione_ancora():
    """Drawn L2 edges are SUCCESSIONE_ANCORA only; events in a box stay sfusi."""
    session = _l2_session(
        cluster=[
            _cluster_row("cl-1843", etichetta="1843", chiave_ordine=10),
            _cluster_row(
                "cl-mattina", parent="cl-1843", etichetta="mattina", chiave_ordine=11
            ),
            _cluster_row(
                "cl-sera", parent="cl-1843", etichetta="sera", chiave_ordine=12
            ),
        ],
        eventi=[
            _evento_row("ev-a", parent="cl-mattina", posizione_doc=1),
            _evento_row("ev-a2", parent="cl-mattina", posizione_doc=2),
            _evento_row("ev-b", parent="cl-sera", posizione_doc=3),
        ],
        successione=[
            _edge_row("succ-ms", "cl-mattina", "cl-sera", "SUCCESSIONE_ANCORA"),
        ],
    )
    body = await grafo_livello2(session)
    by_id = _by_id(body)
    assert by_id["ev-a"]["parent"] == "cl-mattina"
    assert by_id["ev-a2"]["parent"] == "cl-mattina"
    assert by_id["ev-b"]["parent"] == "cl-sera"
    assert _edge_ids(body, "SUCCESSIONE_ANCORA") == {"succ-ms"}
    assert _edge_ids(body, "PRECEDE") == set()
    assert _edge_ids(body, "CONTEMPORANEO") == set()
    tipi = {edge["data"]["tipo"] for edge in body["elements"]["edges"]}
    assert tipi <= {"SUCCESSIONE_ANCORA"}


async def test_m19_fixture_successione_senza_archi_evento():
    """ev-1 e ev-2 condividono cl-1; nessun arco fra eventi. SUCCESSIONE_ANCORA resta."""
    session = _livello2_session()
    body = await grafo_livello2(session, documento="doc-1")
    _assert_livello2_shape(body)
    assert _edge_ids(body, "CONTEMPORANEO") == set()
    assert _edge_ids(body, "PRECEDE") == set()
    assert "succ-ancora-1" in _edge_ids(body, "SUCCESSIONE_ANCORA")


async def test_ordinamento_bande_datato_prima_del_narrativo():
    """Tre cluster, chiavi 100/50/None, posizioni 9/1/0.

    Le due scale non si sommano: i datati per `chiave_ordine` stanno tutti
    prima di chi ha sola `posizione_doc_min`. Atteso: chiave 50, poi 100,
    poi il narrativo (posizione 0).
    """
    session = _l2_session(
        cluster=[
            _cluster_row(
                "cl-100",
                etichetta="tardi",
                chiave_ordine=100,
                posizione_doc_min=9,
            ),
            _cluster_row(
                "cl-50",
                etichetta="prima",
                chiave_ordine=50,
                posizione_doc_min=1,
            ),
            _cluster_row(
                "cl-narr",
                etichetta="solo testo",
                chiave_ordine=None,
                posizione_doc_min=0,
            ),
        ],
        eventi=[
            _evento_row("ev-100", parent="cl-100", posizione_doc=9),
            _evento_row("ev-50", parent="cl-50", posizione_doc=1),
            _evento_row("ev-narr", parent="cl-narr", posizione_doc=0),
        ],
    )
    body = await grafo_livello2(session)
    order = _cluster_ids_in_order(body)
    assert order == ["cl-50", "cl-100", "cl-narr"]
    by_id = _by_id(body)
    assert by_id["cl-50"]["ordine_vista"] == 0
    assert by_id["cl-100"]["ordine_vista"] == 1
    assert by_id["cl-narr"]["ordine_vista"] == 2
    node_ids = [node["data"]["id"] for node in body["elements"]["nodes"]]
    assert node_ids == [
        "cl-50",
        "cl-100",
        "cl-narr",
        "ev-50",
        "ev-100",
        "ev-narr",
    ]


async def test_eventi_stesso_box_per_offset_inizio_non_id():
    """Due eventi nella stessa zona: posizione_doc identica, id in ordine
    inverso rispetto al testo. L'elenco segue offset_inizio, mai l'id.
    """
    session = _l2_session(
        cluster=[_cluster_row("cl-sera", chiave_ordine=10, posizione_doc_min=0)],
        eventi=[
            _evento_row(
                "zzz-late",
                parent="cl-sera",
                posizione_doc=0,
                posizione_chunk=1,
                offset_inizio=90,
            ),
            _evento_row(
                "aaa-early",
                parent="cl-sera",
                posizione_doc=0,
                posizione_chunk=0,
                offset_inizio=10,
            ),
        ],
    )
    body = await grafo_livello2(session)
    event_ids = [
        node["data"]["id"]
        for node in body["elements"]["nodes"]
        if node["data"].get("tipo") == "Fatto"
    ]
    assert event_ids == ["aaa-early", "zzz-late"]
    by_id = _by_id(body)
    assert by_id["aaa-early"]["offset_inizio"] == 10
    assert by_id["zzz-late"]["offset_inizio"] == 90
    assert by_id["aaa-early"]["posizione_doc"] == 0
    assert by_id["zzz-late"]["posizione_doc"] == 0


async def test_successione_ancora_disegnata_contiene_no():
    """SUCCESSIONE_ANCORA is a drawn edge; CONTIENE only assigns parent."""
    session = _l2_session(
        cluster=[
            _cluster_row("cl-a", chiave_ordine=10, ordinale=0),
            _cluster_row("cl-b", chiave_ordine=20, ordinale=1),
        ],
        eventi=[
            _evento_row("ev-a", parent="cl-a", posizione_doc=1),
            _evento_row("ev-b", parent="cl-b", posizione_doc=2),
        ],
        successione=[
            _edge_row("succ-ab", "cl-a", "cl-b", "SUCCESSIONE_ANCORA"),
            _edge_row("contiene-ab", "cl-a", "cl-b", "CONTIENE"),
        ],
    )
    body = await grafo_livello2(session)
    assert _edge_ids(body, "SUCCESSIONE_ANCORA") == {"succ-ab"}
    assert "contiene-ab" not in _edge_ids(body)
    tipi = {edge["data"]["tipo"] for edge in body["elements"]["edges"]}
    assert tipi.isdisjoint({"CONTIENE", "APPARTIENE_A"})


async def test_cluster_pre_mt5_senza_proprieta_nuove_in_coda():
    session = _l2_session(
        cluster=[
            _cluster_row("cl-datato", chiave_ordine=40, posizione_doc_min=2),
            {
                "id": "cl-vecchio",
                "tipo": "AncoraTemporale",
                "documento": "doc-1",
            },
        ],
    )
    body = await grafo_livello2(session)
    by_id = _by_id(body)
    vecchio = by_id["cl-vecchio"]
    for key in _PROPS_ANCORA:
        assert key in vecchio, key
    assert vecchio["chiave_ordine"] is None
    assert vecchio["granularita"] is None
    assert vecchio["stimato"] is None
    assert vecchio["confidenza"] is None
    assert vecchio["descrizione"] is None
    assert vecchio["inizio"] is None
    assert vecchio["fine"] is None
    assert vecchio["posizione_doc_min"] is None
    assert vecchio["natura"] is None
    assert vecchio["ordinale"] is None
    assert _cluster_ids_in_order(body) == ["cl-datato", "cl-vecchio"]
    assert vecchio["ordine_vista"] == 1
    assert by_id["cl-datato"]["ordine_vista"] == 0


async def test_grafo_livello1_e_livello3_invariati():
    l1 = await grafo_livello1(_livello1_session(), documento="doc-1")
    _assert_livello1_shape(l1)
    l3 = await grafo_livello3(_livello3_session(), documento="doc-1")
    _assert_livello3_shape(l3)


async def test_grafo_tutto_invariato_su_fakesession():
    session = FakeSession(
        rows=[
            {
                "id": "ev-1",
                "label": "arrivare",
                "tipo": "Fatto",
                "piano": "PRIMO_PIANO",
                "fattualita": "FATTUALE",
                "documento": "doc-1",
            },
            {
                "id": "m-1",
                "label": "Mario",
                "tipo": "Menzione",
                "documento": "doc-1",
            },
            {
                "id": "r-1",
                "source": "ev-1",
                "target": "m-1",
                "tipo": "SOGG",
                "base": None,
                "segnale": None,
                "superato_da": None,
                "conflitto": None,
                "confidenza": 0.9,
            },
        ]
    )
    result = await grafo(session, documento="doc-1", piano="PRIMO_PIANO", lemma="arrivare")
    ids = {node["data"]["id"] for node in result["elements"]["nodes"]}
    assert ids == {"ev-1", "m-1"}
    assert result["elements"]["edges"][0]["data"]["tipo"] == "SOGG"


async def test_vista_best_effort_su_dati_parziali():
    session = _l2_session(
        cluster=[
            {
                "id": "cl-rotto",
                "tipo": "AncoraTemporale",
                "etichetta": "bozza",
                "chiave_ordine": "non-un-int",
                "posizione_doc_min": "",
                "stimato": None,
                "confidenza": "nope",
                "parent": "cl-rotto",
            }
        ],
        eventi=[
            {
                "id": "ev-rotto",
                "tipo": "Fatto",
                "parent": "cl-rotto",
                "posizione_doc": "x",
            }
        ],
    )
    body = await grafo_livello2(session)
    by_id = _by_id(body)
    assert by_id["cl-rotto"]["chiave_ordine"] is None
    assert by_id["cl-rotto"]["posizione_doc_min"] is None
    assert by_id["cl-rotto"]["confidenza"] is None
    assert "parent" not in by_id["cl-rotto"]
    assert by_id["ev-rotto"]["parent"] == "cl-rotto"
    assert by_id["ev-rotto"]["posizione_doc"] is None
    assert by_id["ev-rotto"]["offset_inizio"] is None
    assert by_id["ev-rotto"]["posizione_chunk"] is None
    assert _edge_ids(body) == set()


async def test_zero_ancore_payload_vuoto_senza_nodo_finto():
    session = _l2_session(
        eventi=[
            _evento_row("ev-orfano", parent=None, posizione_doc=1),
            _evento_row("ev-altro", parent="cl-fantasma", posizione_doc=2),
        ],
    )
    body = await grafo_livello2(session)
    assert body == {"elements": {"nodes": [], "edges": []}}
