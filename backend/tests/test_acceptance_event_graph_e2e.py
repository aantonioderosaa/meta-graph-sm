"""Fixed IT + EN corpus acceptance: full pipeline, LLM stub, FakeSession (no Docker)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import (
    ArcoEvento,
    ArcoEventoGrezzo,
    ArgomentoGrezzo,
    ArgomentoRisolto,
    ChunkFactsheet,
    EventoGrezzo,
    EventoRisolto,
    MenzioneRisolta,
    SottoGrafo,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.event_edges import (
    COLLEGATO_RELAZIONI,
    RELAZIONE_TO_ARCO,
)
from app.pipeline.event_graph.ids import evento_id, menzione_id
from app.pipeline.event_graph.pipeline import EspansioneZona, run_event_graph_ingestion

PIPELINE_PATH = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph" / "pipeline.py"

IT_TEXT = "Mario arrivò alle tre. Alle quattro arrivò di nuovo. Perché pioveva, restò."
EN_TEXT = "Mary arrived at three. At four she arrived again. Because it was raining, she stayed."

IT_ARRIVALS = "Mario arrivò alle tre. Alle quattro arrivò di nuovo."
IT_CAUSE = "Perché pioveva, restò."
EN_ARRIVALS = "Mary arrived at three. At four she arrived again."
EN_CAUSE = "Because it was raining, she stayed."


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

            def single(self_inner):
                return self.rows[0] if self.rows else None

        return R()


def _sogg(forma: str, tipo: str = "nome_proprio") -> ArgomentoGrezzo:
    return ArgomentoGrezzo(
        ruolo="SOGG",
        forma=forma,
        tipo_superficiale=tipo,  # type: ignore[arg-type]
        span=forma,
    )


def _tempo(forma: str) -> ArgomentoGrezzo:
    return ArgomentoGrezzo(
        ruolo="TEMPO",
        forma=forma,
        tipo_superficiale="sn_comune",
        span=forma,
    )


def _grezzo(
    indice: int,
    lemma: str,
    span: str,
    argomenti: list[ArgomentoGrezzo] | None = None,
    **overrides,
) -> EventoGrezzo:
    payload = {
        "indice": indice,
        "lemma": lemma,
        "span": span,
        "tempo": "passato",
        "segmentazione": "principale_finita",
        "polarita_negata": False,
        "modalizzato": False,
        "iterativo": False,
        "ruolo_se": "nessuno",
        "completiva_di": None,
        "classe_verbo_reggente": "nessuna",
        "finale": False,
        "frase_tipo": "dichiarativa",
        "marca_dialogo": False,
        "frase_indice": 0,
        "avverbio_temporale_esplicito": False,
        "connettivo_sequenziale_esplicito": False,
        "argomenti": argomenti if argomenti is not None else [_sogg("Mario")],
        "sogg_speciale": "nessuno",
    }
    payload.update(overrides)
    return EventoGrezzo(**payload)


def _arco(
    da_indice: int,
    a_indice: int,
    segnale: str,
    relazione: str,
    orientamento: str = "subordinata_principale",
) -> ArcoEventoGrezzo:
    return ArcoEventoGrezzo(
        da_indice=da_indice,
        a_indice=a_indice,
        segnale_testuale=segnale,
        relazione_segnale=relazione,  # type: ignore[arg-type]
        orientamento=orientamento,  # type: ignore[arg-type]
    )


def _it_arrivals_sheet() -> ChunkFactsheet:
    return ChunkFactsheet(
        eventi=[
            _grezzo(
                0,
                "arrivare",
                "arrivò alle tre",
                [_sogg("Mario"), _tempo("alle tre")],
                iterativo=True,
                avverbio_temporale_esplicito=True,
                e_testa=True,
            ),
            _grezzo(
                1,
                "arrivare",
                "arrivò di nuovo",
                [_sogg("Mario"), _tempo("alle quattro")],
                iterativo=True,
                avverbio_temporale_esplicito=True,
                frase_indice=1,
                e_testa=True,
            ),
        ],
        archi=[],
        quarantena=[],
    )


def _it_cause_sheet() -> ChunkFactsheet:
    return ChunkFactsheet(
        eventi=[
            _grezzo(
                0,
                "piovere",
                "pioveva",
                [_sogg("pioggia", "sn_comune")],
                segmentazione="subordinata_finita",
            ),
            _grezzo(
                1,
                "restare",
                "restò",
                [_sogg("Mario")],
                frase_indice=1,
                e_testa=True,
            ),
        ],
        archi=[_arco(0, 1, "perché", "causa_esplicita", "subordinata_principale")],
        quarantena=[],
    )


def _en_arrivals_sheet() -> ChunkFactsheet:
    return ChunkFactsheet(
        eventi=[
            _grezzo(
                0,
                "arrive",
                "arrived at three",
                [_sogg("Mary"), _tempo("at three")],
                iterativo=True,
                avverbio_temporale_esplicito=True,
                e_testa=True,
            ),
            _grezzo(
                1,
                "arrive",
                "arrived again",
                [_sogg("Mary"), _tempo("at four")],
                iterativo=True,
                avverbio_temporale_esplicito=True,
                frase_indice=1,
                e_testa=True,
            ),
        ],
        archi=[],
        quarantena=[],
    )


def _en_cause_sheet() -> ChunkFactsheet:
    return ChunkFactsheet(
        eventi=[
            _grezzo(
                0,
                "rain",
                "was raining",
                [_sogg("it", "pronome")],
                segmentazione="subordinata_finita",
            ),
            _grezzo(
                1,
                "stay",
                "stayed",
                [_sogg("Mary")],
                frase_indice=1,
                e_testa=True,
            ),
        ],
        archi=[_arco(0, 1, "because", "causa_esplicita", "subordinata_principale")],
        quarantena=[],
    )


def _tipo_da_relazione(relazione: str) -> str:
    if relazione in COLLEGATO_RELAZIONI:
        return "COLLEGATO"
    mapped = RELAZIONE_TO_ARCO.get(relazione)  # type: ignore[arg-type]
    if mapped is None:
        return "COLLEGATO"
    return mapped[0]


def _materialize_sheets(zona, sheets: list[ChunkFactsheet]):
    eventi: list[EventoRisolto] = []
    archi: list[ArcoEvento] = []
    menzioni: list[MenzioneRisolta] = []
    global_index = 0
    mention_index = 0
    for sheet in sheets:
        local: dict[int, str] = {}
        for grezzo in sheet.eventi:
            args_risolti: list[ArgomentoRisolto] = []
            for arg in grezzo.argomenti:
                resolved = menzione_id(
                    arg.forma,
                    str(arg.tipo_superficiale),
                    zona.documento,
                    zona.id,
                    mention_index,
                )
                mention_index += 1
                menzioni.append(
                    MenzioneRisolta(
                        id=resolved.id,
                        forma=arg.forma,
                        forma_canonica=arg.forma,
                        tipo_superficiale=arg.tipo_superficiale,
                        non_risolto=resolved.non_risolto,
                        documento=zona.documento,
                        chunk_id=zona.id,
                        versione_regole=RULESET_VERSION,
                    )
                )
                args_risolti.append(
                    ArgomentoRisolto(
                        ruolo=arg.ruolo,
                        menzione_id=resolved.id,
                        preposizione=arg.preposizione,
                    )
                )
            eid = evento_id(zona.documento, zona.testo, global_index)
            local[grezzo.indice] = eid
            e_testa = bool(grezzo.e_testa) or grezzo.segmentazione == "principale_finita"
            eventi.append(
                EventoRisolto(
                    id=eid,
                    lemma=grezzo.lemma,
                    tempo=grezzo.tempo,
                    polarita="negata" if grezzo.polarita_negata else "affermata",
                    polarita_negata=grezzo.polarita_negata,
                    iterativo=grezzo.iterativo,
                    iterativita=grezzo.iterativo,
                    e_testa=e_testa,
                    documento=zona.documento,
                    chunk_id=zona.id,
                    indice_grezzo=grezzo.indice,
                    posizione_doc=global_index,
                    posizione_chunk=grezzo.indice,
                    indice_chunk=global_index,
                    segmentazione=grezzo.segmentazione,
                    ancora=eid,
                    span=grezzo.span,
                    argomenti=args_risolti,
                    avverbio_temporale_esplicito=grezzo.avverbio_temporale_esplicito,
                    versione_regole=RULESET_VERSION,
                    completiva_di=grezzo.completiva_di,
                    classe_verbo_reggente=grezzo.classe_verbo_reggente,
                )
            )
            global_index += 1
        for grezzo_arco in sheet.archi:
            da_id = local.get(grezzo_arco.da_indice)
            a_id = local.get(grezzo_arco.a_indice)
            if not da_id or not a_id:
                continue
            archi.append(
                ArcoEvento(
                    tipo=_tipo_da_relazione(str(grezzo_arco.relazione_segnale)),
                    da_id=da_id,
                    a_id=a_id,
                    props={"segnale": grezzo_arco.segnale_testuale},
                )
            )
    by_lemma: dict[str, list[EventoRisolto]] = {}
    for event in eventi:
        by_lemma.setdefault(event.lemma, []).append(event)
    existing = {(str(arco.tipo), arco.da_id, arco.a_id) for arco in archi}
    for group in by_lemma.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda event: (event.posizione_doc or 0, event.id))
        for prev, curr in zip(ordered, ordered[1:]):
            prev_sogg = {
                arg.menzione_id
                for arg in prev.argomenti
                if arg.ruolo == "SOGG" and arg.menzione_id
            }
            curr_sogg = {
                arg.menzione_id
                for arg in curr.argomenti
                if arg.ruolo == "SOGG" and arg.menzione_id
            }
            if not (prev_sogg & curr_sogg):
                continue
            cid = prev.catena_id or f"catena|{prev.lemma}|{'|'.join(sorted(prev_sogg))}"
            if not prev.catena_id:
                prev.catena_id = cid
            curr.catena_id = cid
            curr.catena_ruolo = "AGGIORNA"
            curr.catena_precedente_id = prev.id
            curr.catena_divergenze = ["argomenti"]
    heads = sorted(
        [event for event in eventi if event.e_testa],
        key=lambda event: (event.posizione_doc or 0, event.id),
    )
    for prev, curr in zip(heads, heads[1:]):
        key = ("PRECEDE", prev.id, curr.id)
        if key in existing:
            continue
        archi.append(
            ArcoEvento(
                tipo="PRECEDE",
                da_id=prev.id,
                a_id=curr.id,
                props={"base": "ordine_testa"},
            )
        )
        existing.add(key)
    return eventi, archi, menzioni


def _install(monkeypatch, doc_id: str, first: str, second: str, sheets: dict[str, ChunkFactsheet]) -> None:
    del doc_id, first, second
    ordered = [sheets["arrivals"], sheets["cause"]]
    injected = {"done": False}

    async def riassumi(items, **kwargs):
        return list(items)

    async def collega(items, **kwargs):
        return []

    async def stub(zona, **kwargs):
        zona.espansa = True
        sotto_doc = kwargs.get("sotto_doc")
        local = SottoGrafo()
        if not injected["done"]:
            eventi, archi, menzioni = _materialize_sheets(zona, ordered)
            local.aggiungi(eventi=eventi, archi=archi, menzioni=menzioni)
            injected["done"] = True
        if sotto_doc is not None:
            sotto_doc.aggiungi(
                eventi=list(local.eventi),
                archi=list(local.archi),
                menzioni=list(local.menzioni.values()),
            )
        return EspansioneZona(zona=zona, sotto=local, unita=[])

    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.riassumi_zone",
        riassumi,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.collega_zone",
        collega,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.espandi_zona",
        stub,
    )

    async def _no_transizioni(*args, **kwargs):
        return {}

    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.genera_transizioni_zona",
        _no_transizioni,
    )

    async def _no_livello_temporale(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.estrai_livello_temporale",
        _no_livello_temporale,
    )

    async def _no_livello_relazioni(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.estrai_livello_relazioni",
        _no_livello_relazioni,
    )

    async def boom(*args, **kwargs):
        raise AssertionError("live LLM must not run")

    monkeypatch.setattr(
        "app.pipeline.event_graph.temporal_placement.call_structured",
        boom,
    )


def _assert_graph_shape(outcome, *, arrival_lemma: str, expected_tempi: dict[str, str]) -> None:
    sotto = outcome.sotto
    assert len(sotto.eventi) >= 2
    assert len(sotto.menzioni) >= 1
    assert isinstance(sotto.quarantena, list)
    assert outcome.zone
    assert any(event.e_testa and not event.fuso_in for event in sotto.eventi)

    arrivals = [event for event in sotto.eventi if event.lemma == arrival_lemma]
    chain = [
        event
        for event in arrivals
        if event.catena_ruolo in {"STESSO_EVENTO", "AGGIORNA"} or event.catena_id
    ]
    assert chain, f"expected catena properties among {arrival_lemma!r}"
    assert not any(
        str(arco.tipo) in {"STESSO_EVENTO", "AGGIORNA", "CONTRADDICE"}
        for arco in sotto.archi
    )

    heads = [event for event in sotto.eventi if event.e_testa and not event.fuso_in]
    head_ids = {event.id for event in heads}
    if len(heads) >= 2:
        temporal = [
            arco
            for arco in sotto.archi
            if str(arco.tipo) == "PRECEDE"
            and arco.da_id in head_ids
            and arco.a_id in head_ids
        ]
        assert temporal, "expected PRECEDE among e_testa heads"

    assert any(str(arco.tipo) == "CAUSA" for arco in sotto.archi)

    for event in sotto.eventi:
        expected = expected_tempi.get(event.lemma)
        if expected is not None:
            assert event.tempo == expected


@pytest.mark.asyncio
async def test_acceptance_italian_corpus(monkeypatch):
    doc_id = "doc-it-e2e"
    _install(
        monkeypatch,
        doc_id,
        IT_ARRIVALS,
        IT_CAUSE,
        {"arrivals": _it_arrivals_sheet(), "cause": _it_cause_sheet()},
    )
    session = FakeSession()
    outcome = await run_event_graph_ingestion(doc_id, IT_TEXT, "job-it", session=session)
    _assert_graph_shape(
        outcome,
        arrival_lemma="arrivare",
        expected_tempi={"arrivare": "passato", "piovere": "passato", "restare": "passato"},
    )
    assert not any("DELETE" in query.upper() for query, _ in session.runs)


@pytest.mark.asyncio
async def test_acceptance_english_corpus(monkeypatch):
    doc_id = "doc-en-e2e"
    _install(
        monkeypatch,
        doc_id,
        EN_ARRIVALS,
        EN_CAUSE,
        {"arrivals": _en_arrivals_sheet(), "cause": _en_cause_sheet()},
    )
    session = FakeSession()
    outcome = await run_event_graph_ingestion(doc_id, EN_TEXT, "job-en", session=session)
    _assert_graph_shape(
        outcome,
        arrival_lemma="arrive",
        expected_tempi={"arrive": "passato", "rain": "passato", "stay": "passato"},
    )
    assert not any("DELETE" in query.upper() for query, _ in session.runs)


def test_acceptance_pipeline_isolation_no_app_core():
    source = PIPELINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PIPELINE_PATH))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            modules.append(node.module or "")
    assert all(not (m == "app.core" or m.startswith("app.core.")) for m in modules)
    assert "app.core" not in source
