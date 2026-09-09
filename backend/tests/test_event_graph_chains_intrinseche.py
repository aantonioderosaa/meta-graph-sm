"""AC-C — catene as node properties, not event→event arcs."""

from __future__ import annotations

import pytest

from app.models.event_graph import ArgomentoRisolto, ArcoEvento, EventoRisolto, SottoGrafo
from app.pipeline.event_graph import chains as eg_chains
from app.pipeline.event_graph.chains import (
    CHAIN_TIPI,
    applica,
    applica_persistente,
    catena_id_per,
)
from app.pipeline.event_graph.event_coref import EsitoCoref
from app.pipeline.event_graph.ids import content_hash


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.runs: list[tuple[str, dict]] = []

    async def run(self, query, parameters=None, **params):
        merged = dict(params)
        if parameters is not None and isinstance(parameters, dict):
            merged = {**parameters, **merged}
        self.runs.append((query, merged))

        class R:
            def data(self_inner):
                return self.rows

        return R()


def _sogg(menzione_id: str) -> ArgomentoRisolto:
    return ArgomentoRisolto(ruolo="SOGG", menzione_id=menzione_id)


def _ogg(menzione_id: str) -> ArgomentoRisolto:
    return ArgomentoRisolto(ruolo="OGG", menzione_id=menzione_id)


def _evento(
    event_id: str,
    *,
    lemma: str = "arrivare",
    chunk_id: str = "chunk-a",
    posizione_doc: int = 0,
    posizione_chunk: int = 0,
    menzione_sogg: str = "m-mario",
    argomenti: list[ArgomentoRisolto] | None = None,
    polarita: str | None = "affermata",
    polarita_negata: bool = False,
    fattualita: str | None = "FATTUALE",
    documento: str = "doc-c",
    catena_id: str | None = None,
) -> EventoRisolto:
    if argomenti is None:
        argomenti = [_sogg(menzione_sogg)]
    return EventoRisolto(
        id=event_id,
        lemma=lemma,
        chunk_id=chunk_id,
        posizione_doc=posizione_doc,
        posizione_chunk=posizione_chunk,
        polarita=polarita,
        polarita_negata=polarita_negata,
        fattualita=fattualita,  # type: ignore[arg-type]
        documento=documento,
        argomenti=argomenti,
        catena_id=catena_id,
    )


def _sotto(*eventi: EventoRisolto, archi: list[ArcoEvento] | None = None) -> SottoGrafo:
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=list(eventi), archi=archi or [])
    return sotto


def _chain_arcs(sotto: SottoGrafo) -> list[ArcoEvento]:
    return [arco for arco in sotto.archi if str(arco.tipo) in CHAIN_TIPI]


def test_c1_aggiorna_sets_properties_not_arc():
    candidato = _evento("ev-old", catena_id="catena-shared", argomenti=[_sogg("m-mario"), _ogg("m-lettera")])
    ev = _evento(
        "ev-new",
        chunk_id="chunk-b",
        posizione_doc=1,
        argomenti=[_sogg("m-mario"), _ogg("m-pacco")],
    )
    sotto = _sotto(candidato, ev)
    esito = EsitoCoref(
        kind="Catena",
        nuovo_id=ev.id,
        candidato_id=candidato.id,
        catena_tipo="AGGIORNA",
    )
    applica(sotto, ev, esito)
    assert _chain_arcs(sotto) == []
    assert ev.catena_id == candidato.catena_id
    assert ev.catena_ruolo == "AGGIORNA"
    assert ev.catena_precedente_id == candidato.id
    assert ev.catena_divergenze == ["argomenti"]


def test_c2_stesso_evento_empty_divergenze():
    candidato = _evento("ev-old")
    ev = _evento("ev-new", chunk_id="chunk-b", posizione_doc=1)
    sotto = _sotto(candidato, ev)
    esito = EsitoCoref(
        kind="Catena",
        nuovo_id=ev.id,
        candidato_id=candidato.id,
        catena_tipo="STESSO_EVENTO",
    )
    applica(sotto, ev, esito)
    assert ev.catena_ruolo == "STESSO_EVENTO"
    assert ev.catena_divergenze == []
    assert _chain_arcs(sotto) == []


def test_c3_contraddice_polarita():
    candidato = _evento("ev-old", polarita="affermata")
    ev = _evento(
        "ev-new",
        chunk_id="chunk-b",
        posizione_doc=1,
        polarita="negata",
        polarita_negata=True,
    )
    sotto = _sotto(candidato, ev)
    esito = EsitoCoref(
        kind="Catena",
        nuovo_id=ev.id,
        candidato_id=candidato.id,
        catena_tipo="CONTRADDICE",
    )
    applica(sotto, ev, esito)
    assert ev.catena_ruolo == "CONTRADDICE"
    assert ev.catena_divergenze == ["polarita"]
    assert _chain_arcs(sotto) == []


def test_c4_same_catena_id_across_docs_no_chain_arcs():
    sogg_id = content_hash("vento")
    first = _evento(
        "ev-a",
        lemma="soffiare",
        menzione_sogg=sogg_id,
        documento="doc-a",
        chunk_id="chunk-a",
        posizione_doc=0,
    )
    second = _evento(
        "ev-b",
        lemma="soffiare",
        menzione_sogg=sogg_id,
        documento="doc-b",
        chunk_id="chunk-b",
        posizione_doc=1,
    )
    sotto = _sotto(first, second)
    esito = EsitoCoref(
        kind="Catena",
        nuovo_id=second.id,
        candidato_id=first.id,
        catena_tipo="STESSO_EVENTO",
    )
    applica(sotto, second, esito)
    expected = content_hash(f"soffiare|{sogg_id}")
    assert first.catena_id == expected
    assert second.catena_id == expected
    assert first.catena_id == second.catena_id
    assert catena_id_per(first) == expected
    assert _chain_arcs(sotto) == []


@pytest.mark.asyncio
async def test_c5_applica_persistente_sets_properties_no_merge_arc():
    ev = _evento("ev-new", posizione_doc=3)
    session = FakeSession()
    esito = EsitoCoref(
        kind="Catena",
        nuovo_id="ev-new",
        candidato_id="ev-old",
        catena_tipo="AGGIORNA",
    )
    await applica_persistente(session, ev, esito)
    assert session.runs
    query, params = session.runs[-1]
    assert "SET new.catena_" in query or "new.catena_id" in query
    assert "MERGE (old)-[" not in query
    assert "MERGE ()-[" not in query
    assert "[:AGGIORNA]" not in query
    assert "-[:AGGIORNA]->" not in query
    assert "AGGIORNA" not in query
    assert "DELETE" not in query.upper()
    assert params["old_id"] == "ev-old"
    assert params["new_id"] == "ev-new"
    assert ev.catena_ruolo == "AGGIORNA"
    assert ev.catena_precedente_id == "ev-old"
    assert ev.catena_divergenze == ["argomenti"]


def test_c6_teste_returns_min_position_of_group():
    head = _evento("ev-0", posizione_chunk=0, catena_id="catena-3")
    mid = _evento(
        "ev-1",
        posizione_chunk=1,
        catena_id="catena-3",
    )
    mid.catena_ruolo = "STESSO_EVENTO"
    mid.catena_precedente_id = "ev-0"
    tail = _evento(
        "ev-2",
        posizione_chunk=2,
        catena_id="catena-3",
    )
    tail.catena_ruolo = "AGGIORNA"
    tail.catena_precedente_id = "ev-1"
    sotto = _sotto(head, mid, tail)
    heads = eg_chains.teste(sotto)
    assert [event.id for event in heads] == ["ev-0"]
