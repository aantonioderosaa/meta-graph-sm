"""M19: catalog.py + GET /catalog /stats /graph (no Docker)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

import httpx
import pytest
from httpx import ASGITransport

from app.models.event_graph import (
    GRANULARITA_TEMPORALI,
    Fattualita,
    PianoNarrativo,
    TempoVerbale,
    TipoRelazione,
)
from app.pipeline.event_graph.catalog import (
    catalogo,
    dettaglio_arco,
    dettaglio_nodo,
    grafo,
    grafo_livello1,
    grafo_livello2,
    grafo_livello3,
    stats,
)

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE = BACKEND / "app" / "pipeline" / "event_graph"
CATALOG_PATH = PACKAGE / "catalog.py"
API_PATH = BACKEND / "app" / "api" / "event_graph.py"
LEGACY_FILES = (
    BACKEND / "app" / "core" / "event_bus.py",
    BACKEND / "app" / "core" / "neo4j_client.py",
    BACKEND / "app" / "pipeline" / "ingestion.py",
)


class FakeSession:
    def __init__(self, rows=None, mapping=None):
        self.rows = rows or []
        self.mapping = mapping or {}
        self.runs = []

    def _resolve(self, query: str):
        for key, rows in self.mapping.items():
            if key in query:
                return rows
        return self.rows

    async def run(self, query, parameters=None, **params):
        merged = dict(params)
        if parameters is not None and isinstance(parameters, dict):
            merged = {**parameters, **merged}
        self.runs.append((query, merged))
        rows = self._resolve(query)

        class R:
            def data(self_inner):
                return rows

            def single(self_inner):
                return rows[0] if rows else None

        return R()


class _SessionCtx:
    def __init__(self, session: FakeSession):
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *args) -> None:
        return None


class FakeDriver:
    def __init__(self, session: FakeSession):
        self._session = session

    def session(self) -> _SessionCtx:
        return _SessionCtx(self._session)


def _import_modules(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            base = node.module or ""
            modules.append(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                modules.append(f"{base}.{alias.name}" if base else alias.name)
    return modules


def _is_forbidden_import(module: str) -> bool:
    if module == "app.core" or module.startswith("app.core."):
        return True
    if module == "app.pipeline":
        return True
    if module.startswith("app.pipeline.") and not module.startswith(
        "app.pipeline.event_graph"
    ):
        return True
    return False


def _archi_tipi(payload: dict) -> set[str]:
    arches = payload["arches"]
    found: set[str] = set()
    groups = arches.values() if isinstance(arches, dict) else arches
    for group in groups:
        entries = [group] if isinstance(group, dict) and "tipo" in group else group
        for entry in entries:
            found.add(entry["tipo"])
    return found


def test_catalogo_covers_all_tipo_relazione():
    payload = catalogo()
    found = _archi_tipi(payload)
    expected = set(get_args(TipoRelazione)) | {
        "SUCCESSIONE_ZONA",
        "CONTIENE",
    }
    assert "SATELLITE_DI" in expected
    assert found == expected
    assert "PRECEDE" not in found
    assert "CONTEMPORANEO" not in found
    arches = payload["arches"]
    for famiglia in (
        "argomentali",
        "dizionario",
        "temporale",
        "placeholder",
        "struttura",
    ):
        assert famiglia in arches
        if famiglia == "temporale":
            assert arches[famiglia] == []
            continue
        assert arches[famiglia]
        for entry in arches[famiglia]:
            assert entry["famiglia"] == famiglia
            assert entry["direzione"]
            assert entry["significato"]
    assert "catena" not in arches
    node_ids = {node["id"] for node in payload["nodes"]}
    assert node_ids == {
        "Fatto",
        "Menzione",
        "Quarantena",
        "Zona",
        "AncoraTemporale",
    }
    by_id = {node["id"]: node for node in payload["nodes"]}
    assert by_id["Zona"]["shape"] == "round-rectangle"
    assert by_id["AncoraTemporale"]["shape"] == "round-rectangle"
    succ = next(
        entry
        for entry in payload["arches"]["struttura"]
        if entry["tipo"] == "SUCCESSIONE_ZONA"
    )
    assert succ["direzione"] == "Zona→Zona"
    assert succ["significato"]
    contiene = next(
        entry
        for entry in payload["arches"]["struttura"]
        if entry["tipo"] == "CONTIENE"
    )
    assert contiene["direzione"] == "AncoraTemporale→AncoraTemporale"
    assert contiene["significato"]
    appartiene = next(
        entry
        for entry in payload["arches"]["struttura"]
        if entry["tipo"] == "APPARTIENE_A"
    )
    assert appartiene["direzione"] == "Fatto→AncoraTemporale"
    succ_ancora = next(
        entry
        for entry in payload["arches"]["struttura"]
        if entry["tipo"] == "SUCCESSIONE_ANCORA"
    )
    assert succ_ancora["famiglia"] == "struttura"
    assert succ_ancora["direzione"] == "AncoraTemporale→AncoraTemporale"
    assert "ancore consecutive" in succ_ancora["significato"]
    dizionario_tipi = {entry["tipo"] for entry in payload["arches"]["dizionario"]}
    temporale_tipi = {entry["tipo"] for entry in payload["arches"]["temporale"]}
    assert "PRECEDE" not in dizionario_tipi
    assert "PRECEDE" not in temporale_tipi
    assert "CONTEMPORANEO" not in temporale_tipi
    traits = payload["traits"]
    assert "catena" in traits
    assert set(traits["catena"]["ruoli"]) == {
        "STESSO_EVENTO",
        "AGGIORNA",
        "CONTRADDICE",
    }
    assert traits["tempo"] == list(get_args(TempoVerbale))
    assert traits["polarita"] == ["affermata", "negata"]
    assert False in traits["modalizzato"] and True in traits["modalizzato"]
    assert False in traits["iterativita"] and True in traits["iterativita"]
    assert traits["fattualita"] == list(get_args(Fattualita))
    assert traits["piano"] == list(get_args(PianoNarrativo))
    assert traits["granularita"] == list(GRANULARITA_TEMPORALI)
    assert False in traits["stimato"] and True in traits["stimato"]
    fonte = traits["fonte"]
    fonte_blob = " ".join(fonte) if isinstance(fonte, list) else str(fonte)
    assert "NARRATORE" in fonte_blob
    assert "menzione" in fonte_blob


def test_catalogo_does_not_hit_session():
    session = FakeSession(rows=[{"should": "not be read"}])
    catalogo()
    assert session.runs == []
    assert catalogo.__code__.co_argcount == 0


def _payload_text(payload: dict) -> str:
    chunks: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            chunks.append(value)

    walk(payload)
    return " ".join(chunks)


def test_catalogo_viste_covers_levels_and_meanings():
    payload = catalogo()
    viste = payload["viste"]
    assert set(viste) == {"tutto", "ordine", "temporale", "relazioni", "entita"}
    for key in ("tutto", "ordine", "temporale", "relazioni", "entita"):
        entry = viste[key]
        assert entry["nodi"]
        assert entry["significato"]
    for key in ("tutto", "ordine", "temporale", "relazioni"):
        assert viste[key]["archi"]
    # entita: relazioni fuori scope, nessun arco per design.
    assert viste["entita"]["archi"] == []

    assert set(viste["tutto"]["nodi"]) == {"Fatto", "Menzione", "Quarantena"}
    assert "SUCCESSIONE_ZONA" not in viste["tutto"]["archi"]
    assert "APPARTIENE_A" not in viste["tutto"]["archi"]
    assert "CONTIENE" not in viste["tutto"]["archi"]
    assert "CONTEMPORANEO" not in viste["tutto"]["archi"]
    assert "PRECEDE" not in viste["tutto"]["archi"]

    assert set(viste["ordine"]["nodi"]) == {"Zona", "Fatto"}
    assert set(viste["ordine"]["archi"]) == {
        "SUCCESSIONE_ZONA",
        "SEQUENZA",
        "COLLEGATO",
    }
    assert "CAUSA" not in viste["ordine"]["archi"]
    assert "CONTIENE" not in viste["ordine"]["archi"]

    assert set(viste["temporale"]["nodi"]) == {"AncoraTemporale", "Fatto"}
    # CONTIENE e APPARTIENE_A restano in legenda anche se grafo_livello2 li
    # usa solo per data.parent e non li disegna. SUCCESSIONE_ANCORA è
    # disegnata fra ancore.
    assert set(viste["temporale"]["archi"]) == {
        "SUCCESSIONE_ANCORA",
        "APPARTIENE_A",
        "CONTIENE",
    }
    assert "SEQUENZA" not in viste["temporale"]["archi"]

    assert viste["relazioni"]["nodi"] == ["Fatto"]
    assert set(viste["relazioni"]["archi"]) == {
        "CAUSA",
        "CONDIZIONE",
        "SCOPO",
        "CONCESSIONE",
        "CONTRASTO",
        "LIMITE",
        "CONTENUTO",
    }
    assert "SEQUENZA" not in viste["relazioni"]["archi"]
    assert "CONTIENE" not in viste["relazioni"]["archi"]
    assert "grammatica" in viste["relazioni"]["significato"]
    assert "livello 3" in viste["relazioni"]["significato"]
    assert "SEQUENZA" in viste["relazioni"]["significato"]

    blob = _payload_text(payload)
    assert "stesso momento, non direzionale" not in blob
    assert "successione narrativa fra zone espanse" in blob
    assert "evento appartenente a un'ancora temporale" in blob
    assert "CONTEMPORANEO" not in viste["temporale"]["significato"]
    assert "PRECEDE" not in viste["temporale"]["significato"]
    assert "APPARTIENE_A" in viste["temporale"]["significato"]
    assert "CONTIENE" in viste["temporale"]["significato"]
    assert "SUCCESSIONE_ANCORA" in viste["temporale"]["significato"]
    assert "stimato" in viste["temporale"]["significato"]
    assert "granularita" in viste["temporale"]["significato"]
    assert "ancora temporale che ne contiene un'altra" in blob
    assert "SUCCESSIONE_ZONA" in viste["ordine"]["significato"]


@pytest.mark.asyncio
async def test_stats_fakesession_maps_counts():
    session = FakeSession(
        mapping={
            "count(e)": [{"count(e)": 4}],
            "count(m)": [{"count(m)": 7}],
            "count(q)": [{"count(q)": 1}],
            "type(r)": [{"t": "CAUSA", "n": 2}, {"t": "SUCCESSIONE_ANCORA", "n": 3}],
            "e.piano": [{"p": "PRIMO_PIANO", "n": 3}, {"p": "SFONDO", "n": 1}],
        }
    )
    result = await stats(session)
    assert result["nodi"] == {"Fatto": 4, "Menzione": 7, "Quarantena": 1}
    assert result["archi"]["CAUSA"] == 2
    assert result["archi"]["SUCCESSIONE_ANCORA"] == 3
    assert result["archi"]["SOGG"] == 0
    assert result["tratti"]["piano"]["PRIMO_PIANO"] == 3
    assert result["tratti"]["piano"]["SFONDO"] == 1
    assert result["tratti"]["piano"]["FUORI_LINEA"] == 0
    blob = " ".join(query for query, _ in session.runs)
    assert "MATCH (e:Fatto) RETURN count(e)" in blob
    assert "MATCH (m:Menzione) RETURN count(m)" in blob
    assert "MATCH (q:Quarantena) RETURN count(q)" in blob
    assert "type(r)" in blob
    assert "e.piano" in blob


@pytest.mark.asyncio
async def test_grafo_fakesession_cytoscape_shape():
    session = FakeSession(
        rows=[
            {
                "id": "ev-1",
                "label": "arrivare",
                "tipo": "Fatto",
                "piano": "PRIMO_PIANO",
                "fattualita": "FATTUALE",
                "documento": "doc-1",
                "posizione_doc": 2,
                "posizione_chunk": 1,
                "offset_inizio": 40,
            },
            {
                "id": "ev-fused",
                "label": "andare",
                "tipo": "Fatto",
                "piano": "SFONDO",
                "fattualita": "FATTUALE",
                "documento": "doc-1",
                "fuso_in": "ev-1",
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
    nodes = result["elements"]["nodes"]
    edges = result["elements"]["edges"]
    ids = {node["data"]["id"] for node in nodes}
    assert "ev-1" in ids
    assert "m-1" in ids
    assert "ev-fused" not in ids
    node = next(item for item in nodes if item["data"]["id"] == "ev-1")
    assert node["data"]["label"] == "arrivare"
    assert node["data"]["tipo"] == "Fatto"
    assert node["data"]["piano"] == "PRIMO_PIANO"
    assert node["data"]["fattualita"] == "FATTUALE"
    assert node["data"]["documento"] == "doc-1"
    assert node["data"]["posizione_doc"] == 2
    assert node["data"]["posizione_chunk"] == 1
    assert node["data"]["offset_inizio"] == 40
    assert edges[0]["data"]["id"] == "r-1"
    assert edges[0]["data"]["source"] == "ev-1"
    assert edges[0]["data"]["target"] == "m-1"
    assert edges[0]["data"]["tipo"] == "SOGG"
    assert edges[0]["data"]["confidenza"] == 0.9
    blob = " ".join(query for query, _ in session.runs)
    assert "fuso_in" in blob
    assert "e.posizione_doc AS posizione_doc" in blob
    assert "e.offset_inizio AS offset_inizio" in blob
    assert "m.riassunti AS riassunti" in blob
    assert any(params.get("documento") == "doc-1" for _, params in session.runs)
    assert any(params.get("piano") == "PRIMO_PIANO" for _, params in session.runs)
    assert any(params.get("lemma") == "arrivare" for _, params in session.runs)


@pytest.mark.asyncio
async def test_grafo_menzione_espone_riassunti_e_occorrenze():
    session = FakeSession(
        rows=[
            {
                "id": "m-garage",
                "label": "garage",
                "tipo": "Menzione",
                "documento": "doc-1",
                "descrizione": "lo stesso vecchio garage",
                "occorrenze": 2,
                "riassunti": (
                    '["lo stesso vecchio garage", "nel garage nuovo"]'
                ),
            }
        ]
    )
    result = await grafo(session, documento="doc-1")
    node = result["elements"]["nodes"][0]["data"]
    assert node["label"] == "garage"
    assert node["occorrenze"] == 2
    assert node["riassunti"] == [
        "lo stesso vecchio garage",
        "nel garage nuovo",
    ]
    assert node["descrizione"] == "lo stesso vecchio garage"


@pytest.mark.asyncio
async def test_grafo_drops_edges_whose_endpoint_is_not_a_returned_node():
    """Regression: a macro :Zona<->:Zona SEQUENZA arc (Addendum 2 M2) must
    never surface here — this view only ever returns Fatto/Menzione/
    Quarantena nodes, so an edge pointing at a Zona id would otherwise reach
    Cytoscape with a source/target absent from `nodes` and crash the panel
    ("Can not create edge ... with nonexistant source").
    """
    session = FakeSession(
        mapping={
            "MATCH (e:Fatto)": [
                {
                    "id": "ev-1",
                    "label": "arrivare",
                    "tipo": "Fatto",
                    "piano": "PRIMO_PIANO",
                    "fattualita": "FATTUALE",
                    "documento": "doc-1",
                },
            ],
            "MATCH (a)-[r]->(b)": [
                {
                    "id": "seq-zona",
                    "source": "zona-1",
                    "target": "zona-2",
                    "tipo": "SEQUENZA",
                    "base": None,
                    "segnale": None,
                    "superato_da": None,
                    "conflitto": None,
                },
            ],
        }
    )
    result = await grafo(session)
    ids = {node["data"]["id"] for node in result["elements"]["nodes"]}
    assert ids == {"ev-1"}
    assert result["elements"]["edges"] == []


def _eventi_coppia() -> list[dict]:
    return [
        {
            "id": "ev-a",
            "label": "partire",
            "tipo": "Fatto",
            "piano": "PRIMO_PIANO",
            "fattualita": "FATTUALE",
            "documento": "doc-1",
        },
        {
            "id": "ev-b",
            "label": "arrivare",
            "tipo": "Fatto",
            "piano": "PRIMO_PIANO",
            "fattualita": "FATTUALE",
            "documento": "doc-1",
        },
    ]


def _arco(eid: str, source: str, target: str, tipo: str) -> dict:
    return {
        "id": eid,
        "source": source,
        "target": target,
        "tipo": tipo,
        "base": None,
        "segnale": None,
        "superato_da": None,
        "conflitto": None,
    }


@pytest.mark.asyncio
async def test_grafo_tutto_keeps_sequenza_even_when_pair_has_another_edge():
    session = FakeSession(
        mapping={
            "MATCH (e:Fatto)": _eventi_coppia(),
            "MATCH (a)-[r]->(b)": [
                _arco("seq-ab", "ev-a", "ev-b", "SEQUENZA"),
                _arco("causa-ab", "ev-a", "ev-b", "CAUSA"),
            ],
        }
    )
    result = await grafo(session)
    tipi = {(e["data"]["source"], e["data"]["target"], e["data"]["tipo"]) for e in result["elements"]["edges"]}
    assert ("ev-a", "ev-b", "CAUSA") in tipi
    assert ("ev-a", "ev-b", "SEQUENZA") in tipi


@pytest.mark.asyncio
async def test_grafo_tutto_mostra_sequenza_se_e_l_unico_arco_sulla_coppia():
    session = FakeSession(
        mapping={
            "MATCH (e:Fatto)": _eventi_coppia(),
            "MATCH (a)-[r]->(b)": [
                _arco("seq-ab", "ev-a", "ev-b", "SEQUENZA"),
                _arco("sogg-a", "ev-a", "m-1", "SOGG"),
            ],
        }
    )
    result = await grafo(session)
    tipi = {(e["data"]["source"], e["data"]["target"], e["data"]["tipo"]) for e in result["elements"]["edges"]}
    assert ("ev-a", "ev-b", "SEQUENZA") in tipi


@pytest.mark.asyncio
async def test_dettaglio_nodo_returns_full_property_map():
    session = FakeSession(
        rows=[
            {
                "props": {
                    "id": "ev-1",
                    "lemma": "arrivare",
                    "piano": "PRIMO_PIANO",
                    "e_testa": True,
                    "modalita": "fattuale",
                    "offset_inizio": 120,
                    "offset_fine": 128,
                },
                "labels": ["Fatto"],
            }
        ]
    )
    result = await dettaglio_nodo(session, "ev-1")
    assert result is not None
    assert result["id"] == "ev-1"
    assert result["labels"] == ["Fatto"]
    # every field present in the stored node comes back, unprojected —
    # including ones this test never needs to know the name of in advance.
    assert result["proprieta"]["e_testa"] is True
    assert result["proprieta"]["offset_inizio"] == 120


@pytest.mark.asyncio
async def test_dettaglio_nodo_cluster_temporale_mostra_proprieta_nuove():
    """properties(n) already returns the full map; leftover ClusterTemporale
    nodes (pre-ancore writer) still surface unprojected.
    """
    proprieta = {
        "id": "cl-sera",
        "etichetta": "24 dic, sera",
        "descrizione": "sera del 24 dicembre 1843",
        "granularita": "ora",
        "inizio": "1843-12-24T18",
        "fine": "1843-12-24T23",
        "chiave_ordine": 930540096007,
        "posizione_doc_min": 3,
        "stimato": True,
        "confidenza": 0.72,
        "tipo": "intervallo",
        "documento": "doc-1",
    }
    session = FakeSession(
        rows=[{"props": proprieta, "labels": ["ClusterTemporale"]}]
    )
    result = await dettaglio_nodo(session, "cl-sera")
    assert result is not None
    assert result["id"] == "cl-sera"
    assert result["labels"] == ["ClusterTemporale"]
    assert result["proprieta"]["descrizione"] == "sera del 24 dicembre 1843"
    assert result["proprieta"]["granularita"] == "ora"
    assert result["proprieta"]["inizio"] == "1843-12-24T18"
    assert result["proprieta"]["fine"] == "1843-12-24T23"
    assert result["proprieta"]["chiave_ordine"] == 930540096007
    assert result["proprieta"]["posizione_doc_min"] == 3
    assert result["proprieta"]["stimato"] is True
    assert result["proprieta"]["confidenza"] == 0.72
    assert "catena" not in result


@pytest.mark.asyncio
async def test_dettaglio_nodo_ancora_temporale_mostra_proprieta():
    """AncoraTemporale fake row: labels + natura/ordinale/chiave_ordine."""
    proprieta = {
        "id": "an-sera",
        "etichetta": "24 dic, sera",
        "natura": "esplicita",
        "ordinale": 2,
        "chiave_ordine": 930540096007,
        "tipo": "ora",
        "granularita": "ora",
        "inizio": "1843-12-24T18",
        "fine": "1843-12-24T23",
        "stimato": False,
        "documento": "doc-1",
    }
    session = FakeSession(
        rows=[{"props": proprieta, "labels": ["AncoraTemporale"]}]
    )
    result = await dettaglio_nodo(session, "an-sera")
    assert result is not None
    assert result["id"] == "an-sera"
    assert result["labels"] == ["AncoraTemporale"]
    assert result["proprieta"]["natura"] == "esplicita"
    assert result["proprieta"]["ordinale"] == 2
    assert result["proprieta"]["chiave_ordine"] == 930540096007
    assert result["proprieta"]["etichetta"] == "24 dic, sera"
    assert "catena" not in result


@pytest.mark.asyncio
async def test_dettaglio_nodo_none_when_absent():
    session = FakeSession(rows=[{"props": None, "labels": []}])
    assert await dettaglio_nodo(session, "ghost") is None


@pytest.mark.asyncio
async def test_dettaglio_arco_returns_full_property_map_and_endpoints():
    session = FakeSession(
        rows=[
            {
                "props": {"id": "r-1", "base": "dato_esplicito", "confidenza": 0.82},
                "tipo": "CAUSA",
                "source_id": "ev-1",
                "source_labels": ["Fatto"],
                "source_label": "arrivare",
                "target_id": "ev-2",
                "target_labels": ["Fatto"],
                "target_label": "partire",
            }
        ]
    )
    result = await dettaglio_arco(session, "r-1")
    assert result is not None
    assert result["tipo"] == "CAUSA"
    assert result["proprieta"]["confidenza"] == 0.82
    assert result["source"] == {"id": "ev-1", "labels": ["Fatto"], "label": "arrivare"}
    assert result["target"] == {"id": "ev-2", "labels": ["Fatto"], "label": "partire"}


@pytest.mark.asyncio
async def test_dettaglio_arco_none_when_absent():
    session = FakeSession(rows=[{"props": None, "tipo": None}])
    assert await dettaglio_arco(session, "ghost") is None


@pytest.mark.asyncio
async def test_dettaglio_arco_resolves_synthetic_id_without_rel_id():
    """SOGG / SUCCESSIONE_ZONA / livello-3 have no r.id."""
    row = {
        "props": {
            "regola": "livello_relazioni",
            "livello": "3",
            "spiegazione": "il vento spinge perché il sole ha fallito",
        },
        "tipo": "CAUSA",
        "source_id": "ev-1",
        "source_labels": ["Fatto"],
        "source_label": "soffiare",
        "target_id": "ev-2",
        "target_labels": ["Fatto"],
        "target_label": "cadere",
    }
    session = FakeSession(
        mapping={
            "MATCH (a)-[r {id: $id}]->(b)": [],
            "/* dettaglio_arco_endpoints */": [row],
        }
    )
    arco_id = "CAUSA|ev-1|ev-2"
    result = await dettaglio_arco(session, arco_id)
    assert result is not None
    assert result["id"] == arco_id
    assert result["tipo"] == "CAUSA"
    assert result["proprieta"]["livello"] == "3"
    assert result["proprieta"]["spiegazione"]
    assert result["source"]["id"] == "ev-1"
    assert result["target"]["id"] == "ev-2"
    assert any("dettaglio_arco_endpoints" in query for query, _ in session.runs)


@pytest.mark.asyncio
async def test_dettaglio_arco_synthetic_unknown_still_none():
    session = FakeSession(
        mapping={
            "MATCH (a)-[r {id: $id}]->(b)": [],
            "/* dettaglio_arco_endpoints */": [],
        }
    )
    assert await dettaglio_arco(session, "SOGG|ghost-a|ghost-b") is None


@pytest.mark.asyncio
async def test_get_nodo_and_arco_endpoints_200_and_404(monkeypatch):
    from app.main import app

    session = FakeSession(
        mapping={
            "MATCH (n {id: $id})": [
                {"props": {"id": "ev-1", "lemma": "arrivare"}, "labels": ["Fatto"]}
            ],
            "MATCH (a)-[r {id: $id}]->(b)": [
                {
                    "props": {"id": "r-1"},
                    "tipo": "CAUSA",
                    "source_id": "ev-1",
                    "source_labels": ["Fatto"],
                    "source_label": "arrivare",
                    "target_id": "ev-2",
                    "target_labels": ["Fatto"],
                    "target_label": "partire",
                }
            ],
        }
    )
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(session)
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ok_node = await client.get("/event-graph/nodo/ev-1")
        ok_arc = await client.get("/event-graph/arco/r-1")

    assert ok_node.status_code == 200
    assert ok_node.json()["proprieta"]["lemma"] == "arrivare"
    assert ok_arc.status_code == 200
    assert ok_arc.json()["tipo"] == "CAUSA"

    empty = FakeSession(rows=[])
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(empty)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing_node = await client.get("/event-graph/nodo/ghost")
        missing_arc = await client.get("/event-graph/arco/ghost")
    assert missing_node.status_code == 404
    assert missing_arc.status_code == 404


@pytest.mark.asyncio
async def test_get_catalog_200_without_driver():
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/event-graph/catalog")
    assert response.status_code == 200
    body = response.json()
    assert _archi_tipi(body) == set(get_args(TipoRelazione)) | {
        "SUCCESSIONE_ZONA",
        "CONTIENE",
    }
    assert "PRECEDE" not in _archi_tipi(body)
    assert "CONTEMPORANEO" not in _archi_tipi(body)
    assert {node["id"] for node in body["nodes"]} == {
        "Fatto",
        "Menzione",
        "Quarantena",
        "Zona",
        "AncoraTemporale",
    }
    assert set(body["viste"]) == {"tutto", "ordine", "temporale", "relazioni", "entita"}


@pytest.mark.asyncio
async def test_get_stats_503_without_driver():
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/event-graph/stats")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_get_stats_200_with_stub(monkeypatch):
    session = FakeSession(
        mapping={
            "count(e)": [{"count(e)": 2}],
            "count(m)": [{"count(m)": 3}],
            "count(q)": [{"count(q)": 0}],
            "type(r)": [{"t": "CAUSA", "n": 1}],
            "e.piano": [{"p": "PRIMO_PIANO", "n": 2}],
        }
    )
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(session)
    )
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/event-graph/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["nodi"]["Fatto"] == 2
    assert body["nodi"]["Menzione"] == 3
    assert body["archi"]["CAUSA"] == 1
    assert body["tratti"]["piano"]["PRIMO_PIANO"] == 2


@pytest.mark.asyncio
async def test_get_graph_200_with_stub_excludes_fused(monkeypatch):
    session = FakeSession(
        rows=[
            {
                "id": "live",
                "label": "arrivare",
                "tipo": "Fatto",
                "piano": "PRIMO_PIANO",
                "fattualita": "FATTUALE",
                "documento": "doc-1",
            },
            {
                "id": "fused",
                "label": "fuso",
                "tipo": "Fatto",
                "piano": "SFONDO",
                "fattualita": "FATTUALE",
                "documento": "doc-1",
                "fuso_in": "live",
            },
            {
                "id": "m-1",
                "label": "Mario",
                "tipo": "Menzione",
                "documento": "doc-1",
            },
            {
                "id": "rel-1",
                "source": "live",
                "target": "m-1",
                "tipo": "SOGG",
            },
        ]
    )
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(session)
    )
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/event-graph/graph",
            params={"documento": "doc-1", "piano": "PRIMO_PIANO", "lemma": "arrivare"},
        )
    assert response.status_code == 200
    body = response.json()
    ids = {node["data"]["id"] for node in body["elements"]["nodes"]}
    assert "live" in ids
    assert "fused" not in ids
    edge = body["elements"]["edges"][0]["data"]
    assert edge["id"] == "rel-1"
    assert edge["source"] == "live"
    assert edge["target"] == "m-1"
    blob = " ".join(query for query, _ in session.runs)
    assert "fuso_in" in blob
    assert any(params.get("documento") == "doc-1" for _, params in session.runs)


def test_catalog_isolation_ast():
    source = CATALOG_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(CATALOG_PATH))
    imported = _import_modules(tree)
    violations = [
        f"{CATALOG_PATH}: {module}"
        for module in imported
        if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "app.core" not in source
    assert "app.core.neo4j_client" not in source
    assert "sentence_transformers" not in source
    assert "graphdatascience" not in source.lower()
    assert not any("gds" in module.lower() for module in imported)
    assert "app.models.event_graph" in imported
    api_source = API_PATH.read_text(encoding="utf-8")
    assert '@router.get("/catalog")' in api_source
    assert '@router.get("/stats")' in api_source
    assert '@router.get("/graph")' in api_source
    assert "app.core.neo4j_client" not in api_source


def test_legacy_files_untouched():
    for path in LEGACY_FILES:
        assert path.is_file(), f"legacy file missing: {path}"


_L1_FORBIDDEN_EDGE_TIPI = frozenset(
    {
        "CAUSA",
        "CONTRASTO",
        "CONDIZIONE",
        "SCOPO",
        "CONCESSIONE",
        "LIMITE",
        "CONTENUTO",
    }
)


def _livello1_session() -> FakeSession:
    return FakeSession(
        mapping={
            "grafo_livello1_zone": [
                {
                    "id": "zona-1",
                    "ordinale": 0,
                    "riassunto": "arrivo",
                    "evento_centrale": "ev-1",
                    "documento": "doc-1",
                    "tipo": "Zona",
                },
                {
                    "id": "zona-2",
                    "ordinale": 1,
                    "riassunto": "partenza",
                    "evento_centrale": "ev-2",
                    "documento": "doc-1",
                    "tipo": "Zona",
                },
            ],
            "grafo_livello1_eventi": [
                {
                    "id": "ev-1",
                    "label": "arrivare",
                    "parent": "zona-1",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "posizione_chunk": 0,
                },
                {
                    "id": "ev-1b",
                    "lemma": "fermarsi",
                    "chunk_id": "zona-1",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "posizione_chunk": 1,
                },
                {
                    "id": "ev-2",
                    "label": "partire",
                    "parent": "zona-2",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "posizione_chunk": 0,
                },
                {
                    "id": "ev-fused",
                    "label": "andare",
                    "parent": "zona-1",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "fuso_in": "ev-1",
                },
                {
                    "id": "m-1",
                    "label": "Mario",
                    "tipo": "Menzione",
                    "documento": "doc-1",
                    "parent": "zona-1",
                },
            ],
            "grafo_livello1_successione_zona": [
                {
                    "source": "zona-1",
                    "target": "zona-2",
                    "tipo": "SUCCESSIONE_ZONA",
                    "riassunto_transizione": "dal arrivo alla partenza",
                },
            ],
            "grafo_livello1_ordine_eventi": [
                {
                    "id": "seq-1",
                    "source": "ev-1",
                    "target": "ev-1b",
                    "tipo": "SEQUENZA",
                },
                {
                    "id": "col-1",
                    "source": "ev-1",
                    "target": "ev-1b",
                    "tipo": "COLLEGATO",
                },
                {
                    "id": "causa-1",
                    "source": "ev-1",
                    "target": "ev-1b",
                    "tipo": "CAUSA",
                },
                {
                    "id": "orphan-1",
                    "source": "ev-ghost",
                    "target": "ev-1",
                    "tipo": "SEQUENZA",
                },
            ],
        }
    )


def _assert_livello1_shape(body: dict) -> None:
    nodes = body["elements"]["nodes"]
    edges = body["elements"]["edges"]
    by_id = {node["data"]["id"]: node["data"] for node in nodes}
    assert "zona-1" in by_id
    assert "zona-2" in by_id
    assert by_id["zona-1"]["tipo"] == "Zona"
    assert "parent" not in by_id["zona-1"]
    assert by_id["zona-1"]["ordinale"] == 0
    assert by_id["zona-1"]["riassunto"] == "arrivo"
    assert by_id["zona-1"]["evento_centrale"] == "ev-1"
    assert by_id["ev-1"]["tipo"] == "Fatto"
    assert by_id["ev-1"]["parent"] == "zona-1"
    assert by_id["ev-1b"]["parent"] == "zona-1"
    assert by_id["ev-2"]["parent"] == "zona-2"
    # Il layout preset impila i figli di una zona nell'ordine dell'array nodes:
    # posizione_chunk deve arrivare al client per rendere quell'ordine leggibile.
    assert by_id["ev-1"]["posizione_chunk"] == 0
    assert by_id["ev-1b"]["posizione_chunk"] == 1
    assert by_id["ev-2"]["posizione_chunk"] == 0
    assert "ev-fused" not in by_id
    assert "m-1" not in by_id
    tipi = {edge["data"]["tipo"] for edge in edges}
    assert "SUCCESSIONE_ZONA" in tipi
    succ = next(e["data"] for e in edges if e["data"]["tipo"] == "SUCCESSIONE_ZONA")
    assert succ["riassunto_transizione"] == "dal arrivo alla partenza"
    assert succ["source"] == "zona-1"
    assert succ["target"] == "zona-2"
    assert tipi & {"SEQUENZA", "COLLEGATO"}
    assert tipi.isdisjoint(_L1_FORBIDDEN_EDGE_TIPI)
    assert all(e["data"]["tipo"] not in _L1_FORBIDDEN_EDGE_TIPI for e in edges)
    edge_ids = {edge["data"]["id"] for edge in edges}
    assert "causa-1" not in edge_ids
    assert "orphan-1" not in edge_ids


@pytest.mark.asyncio
async def test_grafo_livello1_fakesession_a_e3_shape():
    session = _livello1_session()
    result = await grafo_livello1(session, documento="doc-1")
    _assert_livello1_shape(result)
    blob = " ".join(query for query, _ in session.runs)
    assert "grafo_livello1_zone" in blob
    assert "grafo_livello1_eventi" in blob
    assert "e.posizione_chunk AS posizione_chunk" in blob
    assert "e.posizione_doc AS posizione_doc" in blob
    assert "e.offset_inizio AS offset_inizio" in blob
    assert "ORDER BY z.ordinale, e.posizione_doc, e.posizione_chunk, e.offset_inizio" in blob
    assert "SUCCESSIONE_ZONA" in blob
    assert "SEQUENZA" in blob
    assert "COLLEGATO" in blob
    assert any(params.get("documento") == "doc-1" for _, params in session.runs)
    assert "Menzione" not in blob
    assert "Quarantena" not in blob
    assert "ClusterTemporale" not in blob


@pytest.mark.asyncio
async def test_get_graph_vista_ordine_200(monkeypatch):
    session = _livello1_session()
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(session)
    )
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/event-graph/graph",
            params={"vista": "ordine", "documento": "doc-1"},
        )
    assert response.status_code == 200
    _assert_livello1_shape(response.json())


_L2_FORBIDDEN_NODE_TIPI = frozenset({"Zona", "Menzione", "Quarantena", "ClusterTemporale"})
_L2_FORBIDDEN_EDGE_TIPI = frozenset(
    {
        "CAUSA",
        "SEQUENZA",
        "COLLEGATO",
        "SUCCESSIONE_ZONA",
        "APPARTIENE_A",
        "CONTIENE",
        "SOGG",
        "CONTRASTO",
        "CONDIZIONE",
        "SCOPO",
        "CONCESSIONE",
        "LIMITE",
        "CONTENUTO",
        "PRECEDE",
        "CONTEMPORANEO",
    }
)
_L2_DRAWN_EDGE_TIPI = frozenset({"SUCCESSIONE_ANCORA"})


def _livello2_session() -> FakeSession:
    return FakeSession(
        mapping={
            "grafo_livello2_cluster": [
                {
                    "id": "cl-1",
                    "label": "1994",
                    "etichetta": "1994",
                    "tipo_cluster": "data",
                    "documento": "doc-1",
                    "tipo": "AncoraTemporale",
                    "natura": "esplicita",
                    "ordinale": 0,
                    "chiave_ordine": 1994,
                    "stimato": False,
                    "granularita": "anno",
                    "inizio": "1994",
                    "fine": None,
                },
                {
                    "id": "cl-2",
                    "label": "1995",
                    "etichetta": "1995",
                    "tipo_cluster": "data",
                    "documento": "doc-1",
                    "tipo": "AncoraTemporale",
                    "natura": "esplicita",
                    "ordinale": 1,
                    "chiave_ordine": 1995,
                    "stimato": False,
                    "granularita": "anno",
                    "inizio": "1995",
                    "fine": None,
                },
                {
                    "id": "zona-1",
                    "ordinale": 0,
                    "riassunto": "arrivo",
                    "tipo": "Zona",
                    "documento": "doc-1",
                },
            ],
            "grafo_livello2_eventi": [
                {
                    "id": "ev-1",
                    "label": "arrivare",
                    "parent": "cl-1",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                },
                {
                    "id": "ev-2",
                    "lemma": "partire",
                    "parent": "cl-1",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                },
                {
                    "id": "ev-3",
                    "label": "ricordare",
                    "parent": None,
                    "documento": "doc-1",
                    "tipo": "Fatto",
                },
                {
                    "id": "ev-fused",
                    "label": "andare",
                    "parent": "cl-1",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "fuso_in": "ev-1",
                },
                {
                    "id": "m-1",
                    "label": "Mario",
                    "tipo": "Menzione",
                    "documento": "doc-1",
                    "parent": "cl-1",
                },
                {
                    "id": "q-1",
                    "label": "frammento",
                    "tipo": "Quarantena",
                    "documento": "doc-1",
                },
            ],
            "grafo_livello2_successione_ancora": [
                {
                    "id": "succ-ancora-1",
                    "source": "cl-1",
                    "target": "cl-2",
                    "tipo": "SUCCESSIONE_ANCORA",
                },
                {
                    "id": "contiene-1",
                    "source": "cl-1",
                    "target": "cl-2",
                    "tipo": "CONTIENE",
                },
            ],
        }
    )


def _assert_livello2_shape(body: dict) -> None:
    nodes = body["elements"]["nodes"]
    edges = body["elements"]["edges"]
    by_id = {node["data"]["id"]: node["data"] for node in nodes}
    assert "cl-1" in by_id
    assert by_id["cl-1"]["tipo"] == "AncoraTemporale"
    assert "parent" not in by_id["cl-1"]
    assert by_id["cl-1"]["etichetta"] == "1994"
    assert by_id["cl-1"]["label"] == "1994"
    assert by_id["cl-1"]["tipo_cluster"] == "data"
    assert by_id["cl-1"]["natura"] == "esplicita"
    assert by_id["cl-1"]["ordinale"] == 0
    assert by_id["cl-1"]["chiave_ordine"] == 1994
    assert by_id["cl-1"]["stimato"] is False
    assert by_id["cl-1"]["granularita"] == "anno"
    assert by_id["cl-1"]["inizio"] == "1994"
    assert "cl-2" in by_id
    assert by_id["cl-2"]["tipo"] == "AncoraTemporale"
    assert "parent" not in by_id["cl-2"]
    assert by_id["ev-1"]["tipo"] == "Fatto"
    assert by_id["ev-1"]["parent"] == "cl-1"
    assert by_id["ev-2"]["parent"] == "cl-1"
    assert by_id["ev-3"]["tipo"] == "Fatto"
    assert "parent" not in by_id["ev-3"]
    assert "ev-fused" not in by_id
    assert "m-1" not in by_id
    assert "q-1" not in by_id
    assert "zona-1" not in by_id
    node_tipi = {node["data"]["tipo"] for node in nodes}
    assert node_tipi.isdisjoint(_L2_FORBIDDEN_NODE_TIPI)
    tipi = {edge["data"]["tipo"] for edge in edges}
    assert "SUCCESSIONE_ANCORA" in tipi
    assert "PRECEDE" not in tipi
    assert "CONTEMPORANEO" not in tipi
    assert tipi <= _L2_DRAWN_EDGE_TIPI
    assert tipi.isdisjoint(_L2_FORBIDDEN_EDGE_TIPI)
    edge_ids = {edge["data"]["id"] for edge in edges}
    assert "succ-ancora-1" in edge_ids
    assert "prec-1" not in edge_ids
    assert "cont-1" not in edge_ids
    assert "prec-old" not in edge_ids
    assert "causa-1" not in edge_ids
    assert "app-1" not in edge_ids
    assert "contiene-1" not in edge_ids
    assert "orphan-1" not in edge_ids
    succ = next(e["data"] for e in edges if e["data"]["id"] == "succ-ancora-1")
    assert succ["source"] == "cl-1"
    assert succ["target"] == "cl-2"
    assert succ["tipo"] == "SUCCESSIONE_ANCORA"


@pytest.mark.asyncio
async def test_grafo_livello2_fakesession_b_e2_shape():
    session = _livello2_session()
    result = await grafo_livello2(session, documento="doc-1")
    _assert_livello2_shape(result)
    blob = " ".join(query for query, _ in session.runs)
    assert "grafo_livello2_cluster" in blob
    assert "grafo_livello2_eventi" in blob
    assert "grafo_livello2_precede" not in blob
    assert "grafo_livello2_contemporaneo" not in blob
    assert "grafo_livello2_successione_ancora" in blob
    assert "PRECEDE" not in blob
    assert "CONTEMPORANEO" not in blob
    assert "APPARTIENE_A" in blob
    assert "SUCCESSIONE_ANCORA" in blob
    assert ":AncoraTemporale" in blob
    assert "ClusterTemporale" not in blob
    assert any(params.get("documento") == "doc-1" for _, params in session.runs)
    assert "Menzione" not in blob
    assert "Quarantena" not in blob
    assert ":Zona" not in blob


@pytest.mark.asyncio
async def test_get_graph_vista_temporale_200(monkeypatch):
    session = _livello2_session()
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(session)
    )
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/event-graph/graph",
            params={"vista": "temporale", "documento": "doc-1"},
        )
    assert response.status_code == 200
    _assert_livello2_shape(response.json())


_L3_FORBIDDEN_NODE_TIPI = frozenset(
    {"Zona", "ClusterTemporale", "AncoraTemporale", "Menzione", "Quarantena"}
)
_L3_FORBIDDEN_EDGE_TIPI = frozenset(
    {
        "COLLEGATO",
        "PRECEDE",
        "SUCCESSIONE_ZONA",
        "APPARTIENE_A",
        "SOGG",
        "CONTEMPORANEO",
        "SEQUENZA",
    }
)
_L3_ALLOWED_EDGE_TIPI = frozenset(
    {
        "CAUSA",
        "CONDIZIONE",
        "SCOPO",
        "CONCESSIONE",
        "CONTRASTO",
        "LIMITE",
        "CONTENUTO",
    }
)


def _livello3_session() -> FakeSession:
    return FakeSession(
        mapping={
            "grafo_livello3_eventi": [
                {
                    "id": "ev-1",
                    "label": "arrivare",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "parent": "zona-1",
                    "posizione_doc": 0,
                },
                {
                    "id": "ev-2",
                    "lemma": "partire",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "posizione_doc": 1,
                },
                {
                    "id": "ev-3",
                    "label": "fermare",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "posizione_doc": 2,
                },
                {
                    "id": "ev-isolato",
                    "label": "solo",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "posizione_doc": 3,
                },
                {
                    "id": "ev-fused",
                    "label": "andare",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "fuso_in": "ev-1",
                },
                {
                    "id": "m-1",
                    "label": "Mario",
                    "tipo": "Menzione",
                    "documento": "doc-1",
                },
                {
                    "id": "zona-1",
                    "ordinale": 0,
                    "riassunto": "arrivo",
                    "tipo": "Zona",
                    "documento": "doc-1",
                },
                {
                    "id": "cl-1",
                    "etichetta": "1994",
                    "tipo": "ClusterTemporale",
                    "documento": "doc-1",
                },
                {
                    "id": "q-1",
                    "label": "frammento",
                    "tipo": "Quarantena",
                    "documento": "doc-1",
                },
            ],
            "grafo_livello3_archi": [
                {
                    "id": "causa-l3",
                    "source": "ev-1",
                    "target": "ev-2",
                    "tipo": "CAUSA",
                    "livello": "3",
                    "spiegazione": "l'arrivo provoca la partenza",
                },
                {
                    "id": "cond-l3",
                    "source": "ev-2",
                    "target": "ev-3",
                    "tipo": "CONDIZIONE",
                    "livello": 3,
                    "spiegazione": "si ferma solo se parte",
                },
                {
                    "id": "causa-no-l3",
                    "source": "ev-1",
                    "target": "ev-3",
                    "tipo": "CAUSA",
                    "spiegazione": "nesso per-zona senza livello 3",
                },
                {
                    "id": "seq-1",
                    "source": "ev-1",
                    "target": "ev-2",
                    "tipo": "SEQUENZA",
                    "base": "esposizione",
                },
                {
                    "id": "seq-2",
                    "source": "ev-2",
                    "target": "ev-3",
                    "tipo": "SEQUENZA",
                    "base": "esposizione",
                },
                {
                    "id": "col-1",
                    "source": "ev-1",
                    "target": "ev-2",
                    "tipo": "COLLEGATO",
                },
                {
                    "id": "prec-1",
                    "source": "ev-1",
                    "target": "ev-2",
                    "tipo": "PRECEDE",
                    "livello": "3",
                },
                {
                    "id": "sz-1",
                    "source": "zona-1",
                    "target": "zona-1",
                    "tipo": "SUCCESSIONE_ZONA",
                },
                {
                    "id": "app-1",
                    "source": "ev-1",
                    "target": "cl-1",
                    "tipo": "APPARTIENE_A",
                },
                {
                    "id": "sogg-1",
                    "source": "ev-1",
                    "target": "m-1",
                    "tipo": "SOGG",
                },
                {
                    "id": "orphan-1",
                    "source": "ev-ghost",
                    "target": "ev-1",
                    "tipo": "CAUSA",
                    "livello": "3",
                    "spiegazione": "endpoint assente",
                },
            ],
        }
    )


def _assert_livello3_shape(body: dict) -> None:
    nodes = body["elements"]["nodes"]
    edges = body["elements"]["edges"]
    by_id = {node["data"]["id"]: node["data"] for node in nodes}
    assert "ev-1" in by_id
    assert "ev-2" in by_id
    assert "ev-3" in by_id
    assert "ev-isolato" in by_id
    assert by_id["ev-1"]["tipo"] == "Fatto"
    assert by_id["ev-2"]["tipo"] == "Fatto"
    assert "parent" not in by_id["ev-1"]
    assert "parent" not in by_id["ev-2"]
    assert "parent" not in by_id["ev-3"]
    assert all("parent" not in node["data"] for node in nodes)
    assert "ev-fused" not in by_id
    assert "m-1" not in by_id
    assert "zona-1" not in by_id
    assert "cl-1" not in by_id
    assert "q-1" not in by_id
    node_tipi = {node["data"]["tipo"] for node in nodes}
    assert node_tipi == {"Fatto"}
    assert node_tipi.isdisjoint(_L3_FORBIDDEN_NODE_TIPI)
    tipi = {edge["data"]["tipo"] for edge in edges}
    assert "CAUSA" in tipi
    assert "CONDIZIONE" in tipi
    assert "SEQUENZA" not in tipi
    assert tipi <= _L3_ALLOWED_EDGE_TIPI
    assert tipi.isdisjoint(_L3_FORBIDDEN_EDGE_TIPI)
    edge_ids = {edge["data"]["id"] for edge in edges}
    assert "causa-l3" in edge_ids
    assert "cond-l3" in edge_ids
    assert "causa-no-l3" in edge_ids
    assert "seq-1" not in edge_ids
    assert "seq-2" not in edge_ids
    assert "col-1" not in edge_ids
    assert "prec-1" not in edge_ids
    assert "sz-1" not in edge_ids
    assert "app-1" not in edge_ids
    assert "sogg-1" not in edge_ids
    assert "orphan-1" not in edge_ids
    causa = next(e["data"] for e in edges if e["data"]["id"] == "causa-l3")
    assert causa["source"] == "ev-1"
    assert causa["target"] == "ev-2"
    assert causa["livello"] == "3"
    assert causa["spiegazione"] == "l'arrivo provoca la partenza"
    cond = next(e["data"] for e in edges if e["data"]["id"] == "cond-l3")
    assert cond["source"] == "ev-2"
    assert cond["target"] == "ev-3"
    assert str(cond["livello"]) == "3"
    assert cond["spiegazione"] == "si ferma solo se parte"
    no_l3 = next(e["data"] for e in edges if e["data"]["id"] == "causa-no-l3")
    assert no_l3["tipo"] == "CAUSA"
    assert "livello" not in no_l3 or no_l3.get("livello") != "3"


@pytest.mark.asyncio
async def test_grafo_livello3_omits_sequenza_even_when_missing():
    session = FakeSession(
        mapping={
            "grafo_livello3_eventi": [
                {
                    "id": "ev-a",
                    "label": "a",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "posizione_doc": 0,
                },
                {
                    "id": "ev-b",
                    "label": "b",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "posizione_doc": 1,
                },
                {
                    "id": "ev-c",
                    "label": "c",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "posizione_doc": 2,
                },
            ],
            "grafo_livello3_archi": [],
        }
    )
    result = await grafo_livello3(session, documento="doc-1")
    nodes = result["elements"]["nodes"]
    edges = result["elements"]["edges"]
    assert {n["data"]["id"] for n in nodes} == {"ev-a", "ev-b", "ev-c"}
    assert edges == []
    assert all(e["data"]["tipo"] != "SEQUENZA" for e in edges)


@pytest.mark.asyncio
async def test_grafo_livello3_does_not_synthesize_sequenza_across_documents():
    session = FakeSession(
        mapping={
            "grafo_livello3_eventi": [
                {
                    "id": "ev-d1a",
                    "label": "a",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "posizione_doc": 0,
                },
                {
                    "id": "ev-d1b",
                    "label": "b",
                    "documento": "doc-1",
                    "tipo": "Fatto",
                    "posizione_doc": 1,
                },
                {
                    "id": "ev-d2a",
                    "label": "c",
                    "documento": "doc-2",
                    "tipo": "Fatto",
                    "posizione_doc": 0,
                },
                {
                    "id": "ev-d2b",
                    "label": "d",
                    "documento": "doc-2",
                    "tipo": "Fatto",
                    "posizione_doc": 1,
                },
            ],
            "grafo_livello3_archi": [],
        }
    )
    result = await grafo_livello3(session)
    sequenza = [
        (e["data"]["source"], e["data"]["target"])
        for e in result["elements"]["edges"]
        if e["data"]["tipo"] == "SEQUENZA"
    ]
    assert sequenza == []
    assert result["elements"]["edges"] == []


@pytest.mark.asyncio
async def test_grafo_livello3_fakesession_c_e2_shape():
    session = _livello3_session()
    result = await grafo_livello3(session, documento="doc-1")
    _assert_livello3_shape(result)
    blob = " ".join(query for query, _ in session.runs)
    assert "grafo_livello3_eventi" in blob
    assert "grafo_livello3_archi" in blob
    assert "posizione_doc" in blob
    assert "spiegazione" in blob
    assert any(params.get("documento") == "doc-1" for _, params in session.runs)
    assert "Menzione" not in blob
    assert "Quarantena" not in blob
    assert "ClusterTemporale" not in blob
    assert ":Zona" not in blob
    assert "parent" not in blob


@pytest.mark.asyncio
async def test_get_graph_vista_relazioni_200(monkeypatch):
    session = _livello3_session()
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(session)
    )
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/event-graph/graph",
            params={"vista": "relazioni", "documento": "doc-1"},
        )
    assert response.status_code == 200
    _assert_livello3_shape(response.json())
