"""MT10 acceptance: ancora pipeline invariants (FakeSession, stubbed LLM)."""

from __future__ import annotations

import ast
import json
from collections import defaultdict
from pathlib import Path
from typing import get_args

import pytest

from app.models.event_graph import (
    NATURA_ANCORE,
    TIPI_ANCORA,
    ArgomentoRisolto,
    EventoRisolto,
    MenzioneRisolta,
    SottoGrafo,
    TipoRelazione,
)
from app.pipeline.event_graph.ancore_estrazione import (
    MAX_CHAR_TESTO_ZONA,
    prepass_regex,
    user_ancore_zona,
)
from app.pipeline.event_graph.ancore_identita import identita_ancora
from app.pipeline.event_graph.ancore_linea import etichetta_normalizzata, violazioni_foresta
from app.pipeline.event_graph.ancore_smistamento import SmistamentoAncore
from app.pipeline.event_graph.catalog import catalogo
from app.pipeline.event_graph.pipeline import esegui_livello_ancore
from app.pipeline.event_graph.zona_segmentation import Zona

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
PACKAGE_DIR = BACKEND / "app" / "pipeline" / "event_graph"
PIPELINE_PATH = PACKAGE_DIR / "pipeline.py"
FIXTURE_SOLE = BACKEND / "tests" / "fixtures" / "sole_vento_riestrazione.json"
CHRISTMAS_CAROL = REPO / "dataset" / "christmas-carol.txt"

TESTO_DATATO = (
    "Nel 1843, la vigilia di Natale, Scrooge chiuse il negozio. "
    "Quella sera ricevette una visita."
)
DOC_DATATO = "doc-carol-mini"


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


async def _llm_vuoto(system, user, schema, **kwargs):
    return schema()


def _zona(testo: str, *, documento: str, zona_id: str = "z-1") -> Zona:
    return Zona(
        id=zona_id,
        documento=documento,
        offset_inizio=0,
        offset_fine=len(testo),
        ordinale=0,
        testo=testo,
    )


def _eventi_datati(documento: str) -> tuple[list[EventoRisolto], list[MenzioneRisolta]]:
    off_chiuse = TESTO_DATATO.find("chiuse")
    off_visita = TESTO_DATATO.find("ricevette")
    menzione = MenzioneRisolta(
        id="m-1843",
        forma="1843",
        documento=documento,
        chunk_id="z-1",
    )
    return (
        [
            EventoRisolto(
                id="ev-chiuse",
                lemma="chiudere",
                span="Scrooge chiuse il negozio",
                posizione_doc=0,
                tempo_assoluto="1843",
                tempo_assoluto_grezzo="1843",
                documento=documento,
                chunk_id="z-1",
                offset_inizio=off_chiuse,
                offset_fine=off_chiuse + len("chiuse"),
                argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-1843")],
            ),
            EventoRisolto(
                id="ev-visita",
                lemma="ricevere",
                span="Quella sera ricevette una visita",
                posizione_doc=1,
                documento=documento,
                chunk_id="z-1",
                offset_inizio=off_visita,
                offset_fine=off_visita + len("ricevette"),
            ),
            EventoRisolto(
                id="ev-fuso",
                lemma="dire",
                posizione_doc=2,
                fuso_in="ev-chiuse",
                documento=documento,
                chunk_id="z-1",
                offset_inizio=off_chiuse,
            ),
        ],
        [menzione],
    )


def _vivi(eventi: list[EventoRisolto]) -> list[EventoRisolto]:
    return [e for e in eventi if e.id and not (e.fuso_in or "").strip()]


def _sotto(
    eventi: list[EventoRisolto],
    menzioni: list[MenzioneRisolta] | None = None,
) -> SottoGrafo:
    grafo = SottoGrafo()
    grafo.aggiungi(eventi=eventi, menzioni=menzioni)
    return grafo


def _foglie(ancore) -> set[str]:
    genitori = {
        etichetta_normalizzata(a.padre) for a in ancore if (a.padre or "").strip()
    }
    return {
        a.etichetta
        for a in ancore
        if a.etichetta and etichetta_normalizzata(a.etichetta) not in genitori
    }


def _figli(ancore, padre: str) -> list:
    chiave = etichetta_normalizzata(padre)
    return [a for a in ancore if etichetta_normalizzata(a.padre) == chiave]


def _sottoalbero_etichette(etichetta: str, ancore) -> list[str]:
    fuori = [etichetta]
    for figlio in _figli(ancore, etichetta):
        if figlio.etichetta:
            fuori.extend(_sottoalbero_etichette(figlio.etichetta, ancore))
    return fuori


def _prima_etichetta_catena(linea) -> str:
    ancore = [a for a in linea.ancore if a.etichetta]
    assert ancore
    dest = {destra for _sinistra, destra in linea.successione}
    teste = [sinistra for sinistra, _destra in linea.successione if sinistra not in dest]
    radici = [a.etichetta for a in ancore if not (a.padre or "").strip()]
    for nome in teste:
        if nome in radici:
            return nome
    if radici:
        chiavi = getattr(linea, "chiave_ordine", None) or {}
        return min(radici, key=lambda nome: chiavi.get(nome, 10**18))
    if teste:
        return teste[0]
    return ancore[0].etichetta


def _evento_offset_minimo(eventi: list[EventoRisolto]) -> EventoRisolto:
    candidati = [e for e in _vivi(eventi) if e.offset_inizio is not None]
    assert candidati
    return min(candidati, key=lambda e: (e.offset_inizio, e.id))


def _eventi_nel_sottoalbero(
    smistamento: SmistamentoAncore, etichetta: str
) -> set[str]:
    nomi = set(_sottoalbero_etichette(etichetta, smistamento.linea.ancore))
    return {
        item.evento_id
        for item in smistamento.appartenenze
        if item.etichetta_foglia in nomi
    }


def _ids_ancore(smistamento: SmistamentoAncore, documento: str) -> set[str]:
    return {
        identita_ancora(a, documento)
        for a in smistamento.linea.ancore
        if a.etichetta
    }


def _merge_params(session: FakeSession, needle: str) -> list[dict]:
    return [params for query, params in session.runs if needle in query]


def _assert_invarianti(
    smistamento: SmistamentoAncore,
    session: FakeSession,
    eventi: list[EventoRisolto],
    *,
    documento: str,
) -> None:
    linea = smistamento.linea
    ancore = list(linea.ancore)
    vivi = _vivi(eventi)
    foglie = _foglie(ancore)
    per_evento = [item.evento_id for item in smistamento.appartenenze]
    vivi_ids = {e.id for e in vivi}
    collocati = set(per_evento)
    assert collocati <= vivi_ids
    assert len(per_evento) == len(collocati)
    assert smistamento.n_non_collocati == len(vivi_ids - collocati)
    for item in smistamento.appartenenze:
        assert item.etichetta_foglia in foglie
    vietate = set(NATURA_ANCORE) | set(TIPI_ANCORA)
    assert all(a.etichetta not in vietate for a in ancore)

    eventi_per_etichetta: dict[str, set[str]] = defaultdict(set)
    for item in smistamento.appartenenze:
        eventi_per_etichetta[item.etichetta_foglia].add(item.evento_id)
    for ancora in ancore:
        sottoalbero = _sottoalbero_etichette(ancora.etichetta, ancore)
        coperti = set()
        for nome in sottoalbero:
            coperti |= eventi_per_etichetta.get(nome, set())
        assert coperti, f"ancora sterile: {ancora.etichetta}"

    assert violazioni_foresta(ancore) == []
    padri: dict[str, list[str]] = defaultdict(list)
    for ancora in ancore:
        if ancora.etichetta:
            padri[etichetta_normalizzata(ancora.padre or "")].append(ancora.etichetta)
    for gruppo in padri.values():
        attesi = [
            (sinistra, destra)
            for sinistra, destra in linea.successione
            if sinistra in gruppo and destra in gruppo
        ]
        assert len(attesi) == max(len(gruppo) - 1, 0)

    for query, params in session.runs:
        assert params.get("regola") != "temporal_placement.esegui"
        assert params.get("segnale") != "ordine_ingestione"
        assert "PRECEDE" not in query
        assert "CONTEMPORANEO" not in query

    apps = _merge_params(session, "MERGE (e)-[r:APPARTIENE_A {id: $rel_id}]->(a)")
    assert {p["e_id"] for p in apps} == collocati
    assert len(apps) == len(collocati)

    nodi = _merge_params(session, "MERGE (a:AncoraTemporale {id: $id})")
    assert {p["id"] for p in nodi} == _ids_ancore(smistamento, documento)


async def _esegui(
    zone: list[Zona],
    eventi: list[EventoRisolto],
    *,
    documento: str,
    job_id: str,
    session: FakeSession | None = None,
    menzioni: list[MenzioneRisolta] | None = None,
) -> tuple[SmistamentoAncore, FakeSession]:
    session = session or FakeSession()
    smistamento = await esegui_livello_ancore(
        zone,
        _sotto(eventi, menzioni),
        documento,
        job_id,
        session,
        call_structured=_llm_vuoto,
    )
    return smistamento, session


@pytest.mark.asyncio
async def test_accettazione_testo_datato_invarianti():
    zone = [_zona(TESTO_DATATO, documento=DOC_DATATO)]
    eventi, menzioni = _eventi_datati(DOC_DATATO)
    smistamento, session = await _esegui(
        zone,
        eventi,
        documento=DOC_DATATO,
        job_id="job-acc-datato",
        menzioni=menzioni,
    )
    _assert_invarianti(smistamento, session, eventi, documento=DOC_DATATO)
    esplicite = [
        a
        for a in smistamento.linea.ancore
        if a.natura == "esplicita" and a.inizio
    ]
    assert any(a.inizio == "1843" for a in esplicite)
    prima = _prima_etichetta_catena(smistamento.linea)
    minimo = _evento_offset_minimo(eventi)
    assert minimo.id == "ev-chiuse"
    assert minimo.id in _eventi_nel_sottoalbero(smistamento, prima)


@pytest.mark.asyncio
async def test_accettazione_stabilita_due_ingestioni():
    zone = [_zona(TESTO_DATATO, documento=DOC_DATATO)]
    eventi, menzioni = _eventi_datati(DOC_DATATO)
    prima, _ = await _esegui(
        zone,
        eventi,
        documento=DOC_DATATO,
        job_id="job-st-1",
        menzioni=menzioni,
    )
    seconda, _ = await _esegui(
        zone,
        eventi,
        documento=DOC_DATATO,
        job_id="job-st-2",
        menzioni=menzioni,
    )
    assert _ids_ancore(prima, DOC_DATATO) == _ids_ancore(seconda, DOC_DATATO)


@pytest.mark.asyncio
async def test_accettazione_sole_vento_povero_di_date():
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
        zone, eventi, documento=documento, job_id="job-acc-sole"
    )
    _assert_invarianti(smistamento, session, eventi, documento=documento)
    assert smistamento.linea.ancore == []
    assert smistamento.appartenenze == []
    assert smistamento.n_non_collocati == len(_vivi(eventi))


def test_accettazione_catalogo_viste_tutto_ordine_relazioni_invariate():
    tipi = set(get_args(TipoRelazione))
    assert "PRECEDE" not in tipi
    assert "CONTEMPORANEO" not in tipi
    viste = catalogo()["viste"]
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


def test_accettazione_christmas_carol_prompt_mai_documento_intero():
    testo = CHRISTMAS_CAROL.read_text(encoding="utf-8")
    assert len(testo) > MAX_CHAR_TESTO_ZONA
    zona = _zona(testo, documento="christmas-carol")
    prompt = user_ancore_zona(zona, regex_hits=prepass_regex(zona.testo[:200]))
    assert testo not in prompt
    assert len(prompt) < len(testo)


def test_pipeline_non_importa_vecchio_percorso_temporale():
    source = PIPELINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PIPELINE_PATH))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            modules.append(node.module or "")
            for alias in node.names:
                if alias.name != "*":
                    modules.append(alias.name)
    blob = " ".join(modules)
    assert "temporal_placement" not in blob
    assert "livello_temporale" not in blob
    assert "estrai_livello_temporale" not in blob
    assert "persisti_livello_temporale" not in blob
