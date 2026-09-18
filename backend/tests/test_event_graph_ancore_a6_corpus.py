"""MT-A6: measured acceptance on corpora that actually contain dates.

FakeSession + stubbed extraction. No Neo4j, no live model.
The fiaba cannot prove year→month nesting; ``dataset/anno-2007.txt`` can.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import get_args

import pytest

from app.models.event_graph import (
    NATURA_ANCORE,
    TIPI_ANCORA,
    AncoraTemporaleProposta,
    EventoRisolto,
    LivelloAncoreResult,
    TipoRelazione,
)
from app.pipeline.event_graph.ancore_estrazione import EstrazioneAncoreResult
from app.pipeline.event_graph.ancore_linea import (
    etichetta_normalizzata,
    granularita_effettiva,
    violazioni_foresta,
)
from app.pipeline.event_graph.ancore_smistamento import ESPRESSIONE_INCERTI_PREFIX
from app.pipeline.event_graph.catalog import _l2_chiave_evento, catalogo, grafo_livello2
from app.pipeline.event_graph.tempo_iso import rango_granularita
from app.pipeline.event_graph.zona_segmentation import Zona
from tests.test_event_graph_ancore_accettazione import (
    CHRISTMAS_CAROL,
    DOC_DATATO,
    FIXTURE_SOLE,
    TESTO_DATATO,
    FakeSession,
    _assert_invarianti,
    _esegui,
    _eventi_datati,
    _eventi_nel_sottoalbero,
    _evento_offset_minimo,
    _ids_ancore,
    _merge_params,
    _prima_etichetta_catena,
    _vivi,
    _zona,
)
from tests.test_event_graph_livello2_vista import (
    _cluster_row,
    _evento_row,
    _l2_session,
)

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
ANNO_2007 = REPO / "dataset" / "anno-2007.txt"
DOC_2007 = "doc-anno-2007"

_MESI_2007 = (
    ("marzo 2007", "2007-03", "aprirono il cantiere", "ev-cantiere", "aprire"),
    ("giugno 2007", "2007-06", "inaugurarono il ponte", "ev-ponte", "inaugurare"),
    ("ottobre 2007", "2007-10", "chiuse la mostra", "ev-mostra", "chiudere"),
)


def _testo_2007() -> str:
    testo = ANNO_2007.read_text(encoding="utf-8")
    assert "2007" in testo
    for mese, _iso, _span, _eid, _lemma in _MESI_2007:
        assert mese.split()[0] in testo
    return testo


def _span(testo: str, ago: str) -> tuple[int, int]:
    inizio = testo.find(ago)
    assert inizio >= 0, ago
    return inizio, inizio + len(ago)


def _proposte_2007(testo: str) -> list[AncoraTemporaleProposta]:
    off_i, off_f = _span(testo, "2007")
    ancore = [
        AncoraTemporaleProposta(
            etichetta="2007",
            natura="esplicita",
            tipo="data",
            inizio="2007",
            granularita="anno",
            espressione="2007",
            offset_inizio=off_i,
            offset_fine=off_f,
            posizione_doc_min=off_i,
            stimato=False,
        )
    ]
    for etichetta, iso, _span_ev, _eid, _lemma in _MESI_2007:
        off_i, off_f = _span(testo, etichetta)
        ancore.append(
            AncoraTemporaleProposta(
                etichetta=etichetta,
                natura="esplicita",
                tipo="data",
                inizio=iso,
                granularita="mese",
                espressione=etichetta,
                offset_inizio=off_i,
                offset_fine=off_f,
                posizione_doc_min=off_i,
                stimato=False,
            )
        )
    return ancore


def _eventi_2007(testo: str, documento: str) -> list[EventoRisolto]:
    fuori: list[EventoRisolto] = []
    for posizione, (_etichetta, iso, span, eid, lemma) in enumerate(_MESI_2007):
        off_i, off_f = _span(testo, span)
        fuori.append(
            EventoRisolto(
                id=eid,
                lemma=lemma,
                span=span,
                posizione_doc=posizione,
                tempo_assoluto=iso,
                documento=documento,
                chunk_id="z-1",
                offset_inizio=off_i,
                offset_fine=off_f,
            )
        )
    return fuori


def _patch_estrazione(monkeypatch, ancore: list[AncoraTemporaleProposta]) -> None:
    async def _estrai(zone, job_id=None, call_structured=None, **_kwargs):
        return EstrazioneAncoreResult(
            livello=LivelloAncoreResult(ancore=list(ancore), segnali=[]),
            n_zone=len(zone or []),
            n_ancore=len(ancore),
        )

    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.estrai_ancore",
        _estrai,
    )


def _assert_successione_senza_rami(linea) -> None:
    padri: dict[str, list[str]] = defaultdict(list)
    for ancora in linea.ancore:
        if ancora.etichetta:
            padri[etichetta_normalizzata(ancora.padre or "")].append(ancora.etichetta)
    uscenti: dict[str, int] = defaultdict(int)
    entranti: dict[str, int] = defaultdict(int)
    for gruppo in padri.values():
        coppie = [
            (sinistra, destra)
            for sinistra, destra in linea.successione
            if sinistra in gruppo and destra in gruppo
        ]
        assert len(coppie) == max(len(gruppo) - 1, 0)
        for sinistra, destra in coppie:
            uscenti[sinistra] += 1
            entranti[destra] += 1
    assert all(grado <= 1 for grado in uscenti.values())
    assert all(grado <= 1 for grado in entranti.values())


def _assert_zero_precede_contemporaneo(session: FakeSession) -> None:
    for query, _params in session.runs:
        assert "PRECEDE" not in query
        assert "CONTEMPORANEO" not in query
    tipi = set(get_args(TipoRelazione))
    assert "PRECEDE" not in tipi
    assert "CONTEMPORANEO" not in tipi


def test_a6_corpus_2007_nomi_mesi_e_anno():
    testo = _testo_2007()
    assert testo.count("2007") >= 4
    assert "marzo" in testo
    assert "giugno" in testo
    assert "ottobre" in testo


def test_a6_christmas_carol_presente_come_corpus_datato():
    assert CHRISTMAS_CAROL.is_file()
    testo = CHRISTMAS_CAROL.read_text(encoding="utf-8")
    assert "Christmas Eve" in testo
    assert "December" in testo
    assert "1843" in TESTO_DATATO
    assert "sera" in TESTO_DATATO


@pytest.mark.asyncio
async def test_a6_2007_anno_padre_dei_mesi(monkeypatch):
    testo = _testo_2007()
    zone = [_zona(testo, documento=DOC_2007)]
    eventi = _eventi_2007(testo, DOC_2007)
    _patch_estrazione(monkeypatch, _proposte_2007(testo))
    smistamento, session = await _esegui(
        zone, eventi, documento=DOC_2007, job_id="job-a6-2007"
    )
    _assert_invarianti(smistamento, session, eventi, documento=DOC_2007)
    _assert_zero_precede_contemporaneo(session)
    _assert_successione_senza_rami(smistamento.linea)
    assert violazioni_foresta(smistamento.linea.ancore) == []

    anno = next(
        a
        for a in smistamento.linea.ancore
        if a.inizio == "2007" and granularita_effettiva(a) == "anno"
    )
    mesi = [
        a
        for a in smistamento.linea.ancore
        if a.inizio and a.inizio.startswith("2007-") and granularita_effettiva(a) == "mese"
    ]
    assert len(mesi) == 3
    for mese in mesi:
        assert etichetta_normalizzata(mese.padre) == etichetta_normalizzata(anno.etichetta)

    for ancora in smistamento.linea.ancore:
        if (ancora.espressione or "").startswith(ESPRESSIONE_INCERTI_PREFIX):
            continue
        padre_nome = (ancora.padre or "").strip()
        if not padre_nome:
            continue
        padre = next(
            a
            for a in smistamento.linea.ancore
            if etichetta_normalizzata(a.etichetta) == etichetta_normalizzata(padre_nome)
        )
        rango_padre = rango_granularita(granularita_effettiva(padre))
        rango_figlio = rango_granularita(granularita_effettiva(ancora))
        assert rango_padre is not None and rango_figlio is not None
        assert rango_padre > rango_figlio

    contiene = _merge_params(session, "MERGE (p)-[r:CONTIENE {id: $rel_id}]->(f)")
    incerti = [
        a
        for a in smistamento.linea.ancore
        if (a.espressione or "").startswith(ESPRESSIONE_INCERTI_PREFIX)
    ]
    assert {etichetta_normalizzata(a.padre) for a in incerti} == {
        etichetta_normalizzata(m.etichetta) for m in mesi
    }
    assert len(incerti) == 3
    assert len(contiene) == 6

    prima = _prima_etichetta_catena(smistamento.linea)
    minimo = _evento_offset_minimo(eventi)
    assert minimo.id == "ev-cantiere"
    assert minimo.id in _eventi_nel_sottoalbero(smistamento, prima)
    vietate = set(NATURA_ANCORE) | set(TIPI_ANCORA)
    assert all(a.etichetta not in vietate for a in smistamento.linea.ancore)


@pytest.mark.asyncio
async def test_a6_2007_due_giri_stessi_id_ancora(monkeypatch):
    testo = _testo_2007()
    zone = [_zona(testo, documento=DOC_2007)]
    eventi = _eventi_2007(testo, DOC_2007)
    _patch_estrazione(monkeypatch, _proposte_2007(testo))
    prima, _ = await _esegui(zone, eventi, documento=DOC_2007, job_id="job-a6-st-1")
    seconda, _ = await _esegui(zone, eventi, documento=DOC_2007, job_id="job-a6-st-2")
    assert _ids_ancore(prima, DOC_2007) == _ids_ancore(seconda, DOC_2007)
    assert _ids_ancore(prima, DOC_2007)


@pytest.mark.asyncio
async def test_a6_zero_precede_e_etichette_vietate_dopo_persist(monkeypatch):
    testo = _testo_2007()
    zone = [_zona(testo, documento=DOC_2007)]
    eventi = _eventi_2007(testo, DOC_2007)
    _patch_estrazione(monkeypatch, _proposte_2007(testo))
    smistamento, session = await _esegui(
        zone, eventi, documento=DOC_2007, job_id="job-a6-zero-rel"
    )
    _assert_zero_precede_contemporaneo(session)
    vietate = set(NATURA_ANCORE) | set(TIPI_ANCORA)
    assert all(a.etichetta not in vietate for a in smistamento.linea.ancore)
    apps = _merge_params(session, "MERGE (e)-[r:APPARTIENE_A {id: $rel_id}]->(a)")
    assert {p["e_id"] for p in apps} == {e.id for e in eventi}


@pytest.mark.asyncio
async def test_a6_carol_mini_esplicita_e_prima_ancora_evento_minimo():
    zone = [_zona(TESTO_DATATO, documento=DOC_DATATO)]
    eventi, menzioni = _eventi_datati(DOC_DATATO)
    smistamento, session = await _esegui(
        zone,
        eventi,
        documento=DOC_DATATO,
        job_id="job-a6-carol",
        menzioni=menzioni,
    )
    _assert_invarianti(smistamento, session, eventi, documento=DOC_DATATO)
    _assert_zero_precede_contemporaneo(session)
    esplicite = [
        a for a in smistamento.linea.ancore if a.natura == "esplicita" and a.inizio
    ]
    assert any(a.inizio == "1843" for a in esplicite)
    prima = _prima_etichetta_catena(smistamento.linea)
    minimo = _evento_offset_minimo(eventi)
    assert minimo.id in _eventi_nel_sottoalbero(smistamento, prima)


@pytest.mark.asyncio
async def test_a6_sole_vento_zero_ancore_nessun_appartiene_a():
    payload = json.loads(FIXTURE_SOLE.read_text(encoding="utf-8"))
    documento = payload["doc_id"]
    zone = [
        Zona(
            id=item["id"],
            documento=item["documento"],
            offset_inizio=item["offset_inizio"],
            offset_fine=item["offset_fine"],
            ordinale=item["ordinale"],
            testo=item["testo"],
            riassunto=item.get("riassunto") or "",
            espansa=bool(item.get("espansa")),
        )
        for item in payload["zone"]
    ]
    eventi = [
        EventoRisolto(
            id=item["id"],
            lemma=item.get("lemma") or "",
            ancora=item.get("ancora"),
            chunk_id=item.get("chunk_id"),
            offset_inizio=item.get("offset_inizio"),
            offset_fine=item.get("offset_fine"),
            posizione_doc=item.get("posizione_doc"),
            posizione_chunk=item.get("posizione_chunk"),
            documento=item.get("documento") or documento,
        )
        for item in payload["eventi"]
    ]
    smistamento, session = await _esegui(
        zone, eventi, documento=documento, job_id="job-a6-sole"
    )
    assert smistamento.linea.ancore == []
    assert smistamento.appartenenze == []
    assert smistamento.n_non_collocati == len(_vivi(eventi))
    assert _merge_params(session, "MERGE (a:AncoraTemporale {id: $id})") == []
    assert _merge_params(session, "MERGE (e)-[r:APPARTIENE_A {id: $rel_id}]->(a)") == []
    _assert_zero_precede_contemporaneo(session)


def test_a6_eventi_nel_box_per_offset_inizio_mai_per_id():
    rango = {"box": 0}
    tardi = {
        "id": "aaa-late",
        "parent": "box",
        "offset_inizio": 90,
        "posizione_doc": 0,
        "posizione_chunk": 0,
    }
    presto = {
        "id": "zzz-early",
        "parent": "box",
        "offset_inizio": 10,
        "posizione_doc": 0,
        "posizione_chunk": 0,
    }
    ordinati = sorted(
        [tardi, presto],
        key=lambda data: _l2_chiave_evento(data, rango, 99),
    )
    assert [item["id"] for item in ordinati] == ["zzz-early", "aaa-late"]


@pytest.mark.asyncio
async def test_a6_vista_livello2_eventi_per_offset_non_id():
    session = _l2_session(
        cluster=[_cluster_row("cl-sera", chiave_ordine=10, posizione_doc_min=0)],
        eventi=[
            _evento_row(
                "aaa-late",
                parent="cl-sera",
                posizione_doc=0,
                posizione_chunk=1,
                offset_inizio=90,
            ),
            _evento_row(
                "zzz-early",
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
        if node["data"].get("tipo") == "Evento"
    ]
    assert event_ids == ["zzz-early", "aaa-late"]


def test_a6_viste_tutto_ordine_relazioni_invariate_senza_precede():
    tipi = set(get_args(TipoRelazione))
    assert "PRECEDE" not in tipi
    assert "CONTEMPORANEO" not in tipi
    viste = catalogo()["viste"]
    assert set(viste["tutto"]["nodi"]) == {"Evento", "Menzione", "Quarantena"}
    assert "PRECEDE" not in viste["tutto"]["archi"]
    assert "CONTEMPORANEO" not in viste["tutto"]["archi"]
    assert set(viste["ordine"]["nodi"]) == {"Zona", "Evento"}
    assert set(viste["ordine"]["archi"]) == {
        "SUCCESSIONE_ZONA",
        "SEQUENZA",
        "COLLEGATO",
    }
    assert viste["relazioni"]["nodi"] == ["Evento"]
    assert set(viste["relazioni"]["archi"]) == {
        "CAUSA",
        "CONDIZIONE",
        "SCOPO",
        "CONCESSIONE",
        "CONTRASTO",
        "LIMITE",
        "CONTENUTO",
    }
