"""M14 unit-per-rule checklist + in-memory integration (no LLM, no Neo4j, no Docker)."""

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
    QuarantenaItem,
    RunState,
    SottoGrafo,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph import chains as eg_chains
from app.pipeline.event_graph.event_coref import candidati, classifica
from app.pipeline.event_graph.event_edges import (
    COLLEGATO_RELAZIONI,
    RELAZIONE_TO_ARCO,
    categorizza,
)
from app.pipeline.event_graph.factuality import applica as applica_fattualita
from app.pipeline.event_graph.ids import evento_id, menzione_id
from app.pipeline.event_graph.narrative_plane import assegna_piano, tempo_base
from app.pipeline.event_graph.persistence import archi_ammissibili, persisti
from app.pipeline.event_graph.pipeline import EspansioneZona, run_event_graph_ingestion
from app.pipeline.event_graph.temporal_placement import esegui, introdurrebbe_ciclo
from app.pipeline.event_graph.wellformed import valida

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"


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


def _sogg_g(forma: str = "Mario", tipo: str = "nome_proprio") -> ArgomentoGrezzo:
    return ArgomentoGrezzo(
        ruolo="SOGG",
        forma=forma,
        tipo_superficiale=tipo,  # type: ignore[arg-type]
        span=forma,
    )


def _arg_g(
    ruolo: str,
    forma: str,
    *,
    tipo: str = "sn_comune",
    preposizione: str | None = None,
) -> ArgomentoGrezzo:
    return ArgomentoGrezzo(
        ruolo=ruolo,  # type: ignore[arg-type]
        forma=forma,
        tipo_superficiale=tipo,  # type: ignore[arg-type]
        span=forma,
        preposizione=preposizione,
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
        "argomenti": argomenti if argomenti is not None else [_sogg_g()],
        "sogg_speciale": "nessuno",
    }
    payload.update(overrides)
    return EventoGrezzo(**payload)


def _sogg(menzione_id: str = "m-mario") -> ArgomentoRisolto:
    return ArgomentoRisolto(ruolo="SOGG", menzione_id=menzione_id)


def _arg(ruolo: str, menzione_id: str, preposizione: str | None = None) -> ArgomentoRisolto:
    return ArgomentoRisolto(
        ruolo=ruolo,  # type: ignore[arg-type]
        menzione_id=menzione_id,
        preposizione=preposizione,
    )


def _evento(
    event_id: str,
    *,
    lemma: str = "arrivare",
    tempo: str | None = "passato",
    piano: str | None = None,
    fattualita: str | None = None,
    fonte: str | None = None,
    polarita: str | None = "affermata",
    polarita_negata: bool = False,
    iterativo: bool = False,
    indice_grezzo: int | None = None,
    posizione_chunk: int = 0,
    posizione_doc: int = 0,
    chunk_id: str = "chunk-a",
    documento: str = "doc-m14",
    completiva_di: int | None = None,
    classe_verbo_reggente: str = "nessuna",
    segmentazione: str | None = "principale_finita",
    ancora: str | None = None,
    span: str | None = None,
    argomenti: list[ArgomentoRisolto] | None = None,
    avverbio: bool = False,
    fuso_in: str | None = None,
    grezzo: str | None = None,
    tempo_assoluto: str | dict | None = None,
    revisioni: list | None = None,
) -> EventoRisolto:
    return EventoRisolto(
        id=event_id,
        lemma=lemma,
        tempo=tempo,  # type: ignore[arg-type]
        piano=piano,  # type: ignore[arg-type]
        fattualita=fattualita,  # type: ignore[arg-type]
        fonte=fonte,
        polarita=polarita,
        polarita_negata=polarita_negata,
        iterativo=iterativo,
        iterativita=iterativo,
        indice_grezzo=indice_grezzo,
        posizione_chunk=posizione_chunk,
        posizione_doc=posizione_doc,
        chunk_id=chunk_id,
        documento=documento,
        completiva_di=completiva_di,
        classe_verbo_reggente=classe_verbo_reggente,  # type: ignore[arg-type]
        segmentazione=segmentazione,  # type: ignore[arg-type]
        ancora=ancora if ancora is not None else event_id,
        span=span or event_id,
        argomenti=argomenti if argomenti is not None else [_sogg()],
        avverbio_temporale_esplicito=avverbio,
        fuso_in=fuso_in,
        tempo_assoluto=tempo_assoluto,
        tempo_assoluto_grezzo=grezzo,
        tempo_assoluto_revisioni=list(revisioni or []),
    )


def _fs(*archi: ArcoEventoGrezzo) -> ChunkFactsheet:
    return ChunkFactsheet(eventi=[], archi=list(archi), quarantena=[])


def _arco_g(
    da_indice: int,
    a_indice: int,
    segnale: str,
    relazione: str,
    orientamento: str = "coordinata",
) -> ArcoEventoGrezzo:
    return ArcoEventoGrezzo(
        da_indice=da_indice,
        a_indice=a_indice,
        segnale_testuale=segnale,
        relazione_segnale=relazione,  # type: ignore[arg-type]
        orientamento=orientamento,  # type: ignore[arg-type]
    )


def _sotto(*eventi: EventoRisolto, archi: list[ArcoEvento] | None = None) -> SottoGrafo:
    graph = SottoGrafo()
    graph.aggiungi(eventi=list(eventi), archi=archi or [])
    return graph


def _plane(eventi: list[EventoRisolto], base: str | None = "passato"):
    applica_fattualita(eventi)
    return assegna_piano(eventi, base)  # type: ignore[arg-type]


def _merge_evento_ids(session: FakeSession) -> set[str]:
    ids: set[str] = set()
    for query, params in session.runs:
        if "MERGE (e:Evento" in query and "id" in params:
            ids.add(str(params["id"]))
    return ids


def _assert_no_delete(session: FakeSession) -> None:
    for query, _ in session.runs:
        upper = query.upper()
        assert "DELETE" not in upper
        assert "DETACH" not in upper


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


def _install_macro_no_llm(monkeypatch) -> None:
    async def riassumi(items, **kwargs):
        return list(items)

    async def collega(items, **kwargs):
        return []

    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.riassumi_zone",
        riassumi,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.collega_zone",
        collega,
    )


def _install_espandi_from_sheets(
    monkeypatch, sheets: list[ChunkFactsheet]
) -> None:
    injected = {"done": False}

    async def stub(zona, **kwargs):
        zona.espansa = True
        sotto_doc = kwargs.get("sotto_doc")
        local = SottoGrafo()
        if not injected["done"]:
            eventi, archi, menzioni = _materialize_sheets(zona, sheets)
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
        "app.pipeline.event_graph.pipeline.espandi_zona",
        stub,
    )


def _boom_temporal(monkeypatch) -> None:
    async def boom(*args, **kwargs):
        raise AssertionError("call_structured must not run")

    monkeypatch.setattr(
        "app.pipeline.event_graph.temporal_placement.call_structured",
        boom,
    )


# --- 1. imperfetto never PRIMO_PIANO -----------------------------------------


def test_imperfetto_never_primo_piano_even_if_tempo_base():
    sheet = ChunkFactsheet(
        eventi=[_grezzo(0, "piovere", "pioveva", tempo="imperfetto")],
        archi=[],
        quarantena=[],
    )
    eventi = [
        _evento("e-impf", lemma="piovere", tempo="imperfetto", indice_grezzo=0),
    ]
    applica_fattualita(eventi, sheet)
    assegna_piano(eventi, "imperfetto")
    assert eventi[0].fattualita == "FATTUALE"
    assert eventi[0].piano == "SFONDO"
    assert eventi[0].piano != "PRIMO_PIANO"


# --- 2. iterativo never PRIMO_PIANO ------------------------------------------


def test_iterativo_never_primo_piano():
    eventi = [_evento("e-it", iterativo=True)]
    _plane(eventi, "passato")
    assert eventi[0].piano == "SFONDO"
    assert eventi[0].piano != "PRIMO_PIANO"


# --- 3. < 3 narrator-FATTUALE → inherit tempo_base ---------------------------


def test_fewer_than_three_narrator_fattuale_inherits_tempo_base():
    eventi = [
        _evento("a", tempo="presente", posizione_chunk=0),
        _evento("b", tempo="presente", posizione_chunk=1),
    ]
    applica_fattualita(eventi)
    assert all(event.fattualita == "FATTUALE" for event in eventi)
    assert all(event.fonte == "NARRATORE" for event in eventi)
    inherited = tempo_base(eventi, RunState(tempo_base_precedente="passato"))
    assert inherited == "passato"
    assert tempo_base(eventi, RunState()) == "presente"


# --- 4. arrivò alle tre / alle quattro → AGGIORNA ----------------------------


def test_arrivo_alle_tre_poi_alle_quattro_catena_aggiorna():
    old = _evento(
        "ev-tre",
        chunk_id="chunk-a",
        posizione_doc=0,
        posizione_chunk=0,
        avverbio=True,
        argomenti=[_sogg("m-mario"), _arg("TEMPO", "m-tre")],
    )
    new = _evento(
        "ev-quattro",
        chunk_id="chunk-b",
        posizione_doc=1,
        posizione_chunk=0,
        avverbio=True,
        argomenti=[_sogg("m-mario"), _arg("TEMPO", "m-quattro")],
    )
    sotto = _sotto(old, new)
    found = candidati(new, sotto.eventi)
    assert [event.id for event in found] == ["ev-tre"]
    esito = classifica(new, found, sotto=sotto)
    assert esito is not None
    assert esito.kind == "Catena"
    assert esito.catena_tipo == "AGGIORNA"
    eg_chains.applica(sotto, new, esito)
    assert not any(str(arco.tipo) == "AGGIORNA" for arco in sotto.archi)
    assert new.catena_ruolo == "AGGIORNA"
    assert new.catena_precedente_id == "ev-tre"
    assert new.catena_divergenze == ["argomenti"]
    assert new.catena_id == old.catena_id


# --- 5. liar: speech FATTUALE for speaker + CONTRADDICE ----------------------


def test_liar_speech_fattuale_for_speaker_and_contraddice():
    speaker = _sogg("m-mario")
    dire = _evento(
        "ev-dire",
        lemma="dire",
        indice_grezzo=0,
        posizione_chunk=0,
        argomenti=[speaker],
    )
    inner = _evento(
        "ev-partire",
        lemma="partire",
        indice_grezzo=1,
        posizione_chunk=1,
        completiva_di=0,
        classe_verbo_reggente="nessuna",
        argomenti=[speaker],
    )
    applica_fattualita([dire, inner])
    assert inner.fonte == "m-mario"
    assert inner.fonte != "NARRATORE"
    assert inner.fattualita == "FATTUALE"
    edges = categorizza(_fs(), [dire, inner])
    assert any(
        arco.tipo == "CONTENUTO" and arco.da_id == "ev-dire" and arco.a_id == "ev-partire"
        for arco in edges.archi
    )

    said = _evento(
        "ev-said",
        lemma="partire",
        chunk_id="chunk-speech",
        polarita="affermata",
        fonte="m-mario",
        fattualita="FATTUALE",
        argomenti=[speaker],
    )
    narrator = _evento(
        "ev-truth",
        lemma="partire",
        chunk_id="chunk-narr",
        posizione_doc=1,
        polarita="negata",
        polarita_negata=True,
        fonte="NARRATORE",
        fattualita="FATTUALE",
        argomenti=[speaker],
    )
    sotto = _sotto(said, narrator)
    esito = classifica(narrator, candidati(narrator, sotto.eventi), sotto=sotto)
    assert esito is not None
    assert esito.kind == "Catena"
    assert esito.catena_tipo == "CONTRADDICE"


# --- 6. perché / causa_esplicita → CAUSA sub→main ----------------------------


def test_perche_causa_esplicita_sub_to_main():
    main = _evento("restare", indice_grezzo=0, lemma="restare")
    sub = _evento(
        "piovere",
        indice_grezzo=1,
        lemma="piovere",
        segmentazione="subordinata_finita",
    )
    result = categorizza(
        _fs(_arco_g(1, 0, "perché", "causa_esplicita", "subordinata_principale")),
        [main, sub],
    )
    assert [(arco.tipo, arco.da_id, arco.a_id) for arco in result.archi] == [
        ("CAUSA", "piovere", "restare")
    ]
    assert result.quarantena == []


# --- 7. quindi / consecuzione → CAUSA previous→following ---------------------


def test_quindi_consecuzione_previous_to_following():
    prev = _evento("cadere", indice_grezzo=0, lemma="cadere")
    foll = _evento("alzare", indice_grezzo=1, lemma="alzare")
    result = categorizza(
        _fs(_arco_g(0, 1, "quindi", "consecuzione")),
        [prev, foll],
    )
    assert [(arco.tipo, arco.da_id, arco.a_id) for arco in result.archi] == [
        ("CAUSA", "cadere", "alzare")
    ]


# --- 8. mentre / temporale_ambiguo → COLLEGATO -------------------------------


def test_mentre_temporale_ambiguo_collegato():
    left = _evento("cucinare", indice_grezzo=0, lemma="cucinare")
    right = _evento("parlare", indice_grezzo=1, lemma="parlare")
    result = categorizza(
        _fs(_arco_g(0, 1, "mentre", "temporale_ambiguo")),
        [left, right],
    )
    assert len(result.archi) == 1
    assert result.archi[0].tipo == "COLLEGATO"
    assert result.archi[0].props["segnale"] == "mentre"


# --- 9. dire CONTENUTO partire is not a SEQUENZA step ------------------------


def test_dire_contenuto_partire_not_sequenza():
    dire = _evento("dire", indice_grezzo=0, lemma="dire")
    partire = _evento(
        "partire",
        indice_grezzo=1,
        lemma="partire",
        completiva_di=0,
    )
    result = categorizza(_fs(), [dire, partire])
    tipi = [arco.tipo for arco in result.archi]
    assert "CONTENUTO" in tipi
    assert "SEQUENZA" not in tipi
    assert all(
        not (arco.tipo == "SEQUENZA" and {arco.da_id, arco.a_id} == {"dire", "partire"})
        for arco in result.archi
    )
    assert partire.contenuto_di == "dire"


# --- 10. arcs do not cross nesting -------------------------------------------


def test_completiva_mismatch_no_causa_across_nesting():
    parent = _evento("dire", indice_grezzo=0, lemma="dire")
    nested = _evento("partire", indice_grezzo=1, lemma="partire", completiva_di=0)
    sibling = _evento("restare", indice_grezzo=2, lemma="restare")
    result = categorizza(
        _fs(_arco_g(1, 2, "perché", "causa_esplicita", "subordinata_principale")),
        [parent, nested, sibling],
    )
    assert not any(
        arco.tipo == "CAUSA" and {arco.da_id, arco.a_id} == {"partire", "restare"}
        for arco in result.archi
    )


# --- 11. CAUSA cycle → quarantena, cycle broken ------------------------------


def test_causa_cycle_quarantena_cycle_broken():
    a = _evento("a", indice_grezzo=0, lemma="a")
    b = _evento("b", indice_grezzo=1, lemma="b")
    result = categorizza(
        _fs(
            _arco_g(0, 1, "perché", "causa_esplicita"),
            _arco_g(1, 0, "perché", "causa_esplicita"),
        ),
        [a, b],
        testo_chunk="A perché B perché A",
    )
    causa = [(arco.da_id, arco.a_id) for arco in result.archi if arco.tipo == "CAUSA"]
    assert ("a", "b") in causa
    assert ("b", "a") not in causa
    assert any(item.motivo == "ciclo CAUSA" for item in result.quarantena)

    eventi = [
        _evento("ev-a", lemma="a", piano="PRIMO_PIANO"),
        _evento("ev-b", lemma="b", piano="PRIMO_PIANO", posizione_chunk=1),
    ]
    archi = [
        ArcoEvento(tipo="CAUSA", da_id="ev-a", a_id="ev-b"),
        ArcoEvento(tipo="CAUSA", da_id="ev-b", a_id="ev-a"),
    ]
    q = valida(eventi, archi)
    pairs = {(arco.da_id, arco.a_id) for arco in archi if arco.tipo == "CAUSA"}
    assert not (("ev-a", "ev-b") in pairs and ("ev-b", "ev-a") in pairs)
    assert any(item.motivo == "ciclo CAUSA" for item in q)


# --- 12. biforcazione → two heads --------------------------------------------


def test_biforcazione_two_heads():
    a = _evento("A", lemma="arrivare", posizione_chunk=0)
    b = _evento("B", lemma="arrivare", posizione_chunk=1)
    c = _evento("C", lemma="arrivare", posizione_chunk=2)
    d = _evento("D", lemma="guardare", argomenti=[_sogg("m-d")], posizione_chunk=3)
    e = _evento("E", lemma="guardare", argomenti=[_sogg("m-d")], posizione_chunk=4)
    a.catena_id = "cat-arrivo"
    b.catena_id = "cat-arrivo"
    b.catena_ruolo = "STESSO_EVENTO"
    b.catena_precedente_id = "A"
    c.catena_id = "cat-arrivo"
    c.catena_ruolo = "AGGIORNA"
    c.catena_precedente_id = "A"
    d.catena_id = "cat-guarda"
    e.catena_id = "cat-guarda"
    e.catena_ruolo = "CONTRADDICE"
    e.catena_precedente_id = "D"
    sotto = _sotto(a, b, c, d, e)
    heads = {event.id for event in eg_chains.teste(sotto)}
    assert heads == {"A", "D"}
    forks = {event.id for event in eg_chains.biforcazioni(sotto)}
    assert forks == {"A"}


# --- 13. orphan last in chunk → COLLEGATO ordine_menzione --------------------


def test_orphan_last_in_chunk_collegato_not_quarantined():
    previous = _evento(
        "prev",
        piano="PRIMO_PIANO",
        posizione_chunk=0,
        argomenti=[_sogg("m-prev")],
    )
    orphan = _evento(
        "last",
        piano="PRIMO_PIANO",
        posizione_chunk=1,
        argomenti=[_sogg("m-last")],
    )
    eventi = [previous, orphan]
    archi: list[ArcoEvento] = []
    q = valida(eventi, archi)
    assert q == []
    assert [event.id for event in eventi] == ["prev", "last"]
    assert len(archi) == 1
    assert archi[0].tipo == "COLLEGATO"
    assert archi[0].da_id == "prev"
    assert archi[0].a_id == "last"
    assert archi[0].props["segnale"] == "ordine_menzione"


# --- Integration without Docker ----------------------------------------------


def _idempotence_sheet() -> ChunkFactsheet:
    return ChunkFactsheet(
        eventi=[
            _grezzo(
                0,
                "arrivare",
                "arrivò alle tre",
                [_sogg_g("Mario"), _arg_g("TEMPO", "alle tre")],
            ),
            _grezzo(
                1,
                "restare",
                "restò",
                [_sogg_g("Mario")],
                frase_indice=1,
            ),
        ],
        archi=[],
        quarantena=[],
    )


@pytest.mark.asyncio
async def test_idempotence_second_run_merge_ids_subset_no_delete(monkeypatch):
    testo = "Mario arrivò alle tre. Poi restò."
    doc_id = "doc-idem"
    _install_macro_no_llm(monkeypatch)
    _install_espandi_from_sheets(monkeypatch, [_idempotence_sheet()])
    _boom_temporal(monkeypatch)

    first = FakeSession()
    second = FakeSession()
    await run_event_graph_ingestion(doc_id, testo, "job-idemp-1", session=first)
    await run_event_graph_ingestion(doc_id, testo, "job-idemp-2", session=second)

    ids1 = _merge_evento_ids(first)
    ids2 = _merge_evento_ids(second)
    assert ids1
    assert ids2 <= ids1
    _assert_no_delete(first)
    _assert_no_delete(second)


@pytest.mark.asyncio
async def test_monotonicity_second_persist_no_delete_detach(monkeypatch):
    testo = "Mario arrivò alle tre. Poi restò."
    doc_id = "doc-mono"
    _install_macro_no_llm(monkeypatch)
    _install_espandi_from_sheets(monkeypatch, [_idempotence_sheet()])
    _boom_temporal(monkeypatch)
    session = FakeSession()
    await run_event_graph_ingestion(doc_id, testo, "job-mono-1", session=session)
    await run_event_graph_ingestion(doc_id, testo, "job-mono-2", session=session)
    _assert_no_delete(session)
    assert all("DELETE" not in q.upper() and "DETACH" not in q.upper() for q, _ in session.runs)


def test_parallel_spines_archi_ammissibili_drops_cross_doc_sequenza():
    ev_a = _evento("ev-a", documento="doc-a", lemma="arrivare")
    ev_b = _evento(
        "ev-b",
        documento="doc-b",
        chunk_id="chunk-b",
        lemma="partire",
        posizione_doc=1,
        argomenti=[_sogg("m-b")],
    )
    sotto = _sotto(
        ev_a,
        ev_b,
        archi=[
            ArcoEvento(tipo="SEQUENZA", da_id="ev-a", a_id="ev-b"),
            ArcoEvento(
                tipo="PRECEDE",
                da_id="ev-a",
                a_id="ev-b",
                props={"base": "dato_esplicito"},
            ),
            ArcoEvento(
                tipo="COLLEGATO",
                da_id="ev-a",
                a_id="ev-b",
                props={"segnale": "ordine_ingestione"},
            ),
        ],
    )
    allowed = {(arco.tipo, arco.da_id, arco.a_id) for arco in archi_ammissibili(sotto)}
    assert ("SEQUENZA", "ev-a", "ev-b") not in allowed
    assert ("PRECEDE", "ev-a", "ev-b") in allowed
    assert ("COLLEGATO", "ev-a", "ev-b") in allowed


@pytest.mark.asyncio
async def test_temporal_refinement_in_memory_placeholder_then_precede(monkeypatch):
    _boom_temporal(monkeypatch)
    previous = _evento(
        "ev-p",
        lemma="parlare",
        posizione_doc=0,
        grezzo="1990",
        tempo_assoluto="1990",
        revisioni=["1990"],
        argomenti=[_sogg("m-shared")],
    )
    event_a = _evento(
        "ev-a",
        lemma="arrivare",
        posizione_doc=1,
        argomenti=[_sogg("m-shared")],
    )
    sotto = _sotto(previous, event_a)
    first = await esegui(None, sotto, "job-ref-1")
    placeholders = [
        arco
        for arco in sotto.archi
        if str(arco.tipo) == "COLLEGATO"
        and arco.props.get("segnale") == "ordine_ingestione"
    ]
    assert placeholders
    assert first.quarantena == [] or isinstance(first.quarantena, list)

    event_a.tempo_assoluto_grezzo = "1994-04-06"
    event_b = _evento(
        "ev-b",
        lemma="restare",
        posizione_doc=2,
        grezzo="1994-04-06",
        argomenti=[_sogg("m-shared")],
    )
    sotto.aggiungi(eventi=[event_b])
    before_rev = list(event_a.tempo_assoluto_revisioni)
    second = await esegui(None, sotto, "job-ref-2")
    precedes = [arco for arco in sotto.archi if str(arco.tipo) == "PRECEDE"]
    assert precedes
    assert any(arco.props.get("superato_da") for arco in placeholders)
    assert len(event_a.tempo_assoluto_revisioni) > len(before_rev)
    assert event_a.tempo == "passato"
    assert event_b.tempo == "passato"
    assert second.archi_aggiunti or precedes
    assert all(
        arco.props.get("versione_regole") == RULESET_VERSION
        for arco in precedes
        if "versione_regole" in arco.props
    )


@pytest.mark.asyncio
async def test_chronological_cycle_quarantena_motivo_prefix(monkeypatch):
    _boom_temporal(monkeypatch)
    a = _evento("ev-a", grezzo="1994", posizione_doc=0)
    b = _evento("ev-b", grezzo="1990", posizione_doc=1, argomenti=[_sogg("m-x")])
    a.argomenti = [_sogg("m-x")]
    existing = ArcoEvento(
        tipo="PRECEDE",
        da_id="ev-a",
        a_id="ev-b",
        props={"base": "connettivo"},
    )
    sotto = _sotto(a, b, archi=[existing])
    assert introdurrebbe_ciclo(sotto.archi, "ev-b", "ev-a") is not None
    outcome = await esegui(None, sotto, "job-cyc")
    assert outcome.quarantena
    assert all(item.motivo.startswith("ciclo cronologico") for item in outcome.quarantena)
    assert all(
        not (arco.da_id == "ev-b" and arco.a_id == "ev-a" and str(arco.tipo) == "PRECEDE")
        for arco in sotto.archi
    )


@pytest.mark.asyncio
async def test_ruleset_version_written_on_persist_and_outcome(monkeypatch):
    testo = "Mario arrivò alle tre. Poi restò."
    doc_id = "doc-ver"
    _install_macro_no_llm(monkeypatch)
    _install_espandi_from_sheets(monkeypatch, [_idempotence_sheet()])
    _boom_temporal(monkeypatch)
    session = FakeSession()
    outcome = await run_event_graph_ingestion(doc_id, testo, "job-ver", session=session)
    assert isinstance(outcome.sotto.quarantena, list)
    evento_runs = [(q, p) for q, p in session.runs if "MERGE (e:Evento" in q]
    assert evento_runs
    assert all(p.get("versione_regole") == RULESET_VERSION for _, p in evento_runs)
    for event in outcome.sotto.eventi:
        if event.versione_regole:
            assert event.versione_regole == RULESET_VERSION


@pytest.mark.asyncio
async def test_persisti_twice_same_graph_no_delete():
    mention_id = "m-mario"
    event = _evento("ev-1", argomenti=[_sogg(mention_id)])
    graph = SottoGrafo()
    graph.aggiungi(
        eventi=[event],
        quarantena=[
            QuarantenaItem(
                id="q-1",
                frammento="…",
                motivo="ancora assente",
                ancora_doc="doc-m14",
                ancora_chunk="chunk-a",
            )
        ],
    )
    first = FakeSession()
    second = FakeSession()
    await persisti(first, graph, job_id="job-p1")
    await persisti(second, graph, job_id="job-p1")
    assert _merge_evento_ids(second) <= _merge_evento_ids(first)
    _assert_no_delete(first)
    _assert_no_delete(second)


def test_m14_isolation_ast_event_graph_package():
    violations: list[str] = []
    for path in PACKAGE_DIR.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                modules.append(node.module or "")
            for module in modules:
                if module == "app.core" or module.startswith("app.core."):
                    violations.append(f"{path}: {module}")
                if module == "app.pipeline" or (
                    module.startswith("app.pipeline.")
                    and not module.startswith("app.pipeline.event_graph")
                ):
                    violations.append(f"{path}: {module}")
    assert violations == []
