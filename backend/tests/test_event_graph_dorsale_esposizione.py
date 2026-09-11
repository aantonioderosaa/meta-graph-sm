"""Exposition backbone tests: collega_dorsale_eventi (no Docker, no LLM)."""

from __future__ import annotations

from pathlib import Path

from app.models.event_graph import ArcoEvento, EventoRisolto, SottoGrafo
from app.pipeline.event_graph import RULESET_VERSION, pipeline
from app.pipeline.event_graph.persistence import archi_ammissibili
from app.pipeline.event_graph.pipeline import (
    BASE_DORSALE_ESPOSIZIONE,
    REGOLA_DORSALE_ESPOSIZIONE,
    collega_dorsale_eventi,
)
from app.pipeline.event_graph.zona_segmentation import Zona

DOC = "doc-dorsale"
PIPELINE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "pipeline"
    / "event_graph"
    / "pipeline.py"
)


def _zona(ordinale: int, **overrides) -> Zona:
    payload = {
        "id": f"z-{ordinale}",
        "documento": DOC,
        "offset_inizio": ordinale * 100,
        "offset_fine": ordinale * 100 + 100,
        "ordinale": ordinale,
        "testo": f"zona {ordinale}",
        "espansa": True,
    }
    payload.update(overrides)
    return Zona(**payload)


def _evento(
    event_id: str,
    zona_id: str,
    posizione_chunk: int,
    *,
    fuso_in: str | None = None,
) -> EventoRisolto:
    return EventoRisolto(
        id=event_id,
        lemma="soffiare",
        documento=DOC,
        chunk_id=zona_id,
        posizione_chunk=posizione_chunk,
        posizione_doc=posizione_chunk,
        offset_inizio=posizione_chunk * 10,
        e_testa=True,
        fuso_in=fuso_in,
    )


def _sotto(eventi, archi=None) -> SottoGrafo:
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=list(eventi), archi=list(archi or []))
    return sotto


def _dorsale(sotto: SottoGrafo) -> list[ArcoEvento]:
    return [
        arco
        for arco in sotto.archi
        if arco.props.get("regola") == REGOLA_DORSALE_ESPOSIZIONE
    ]


def _coppie(archi) -> set[tuple[str, str]]:
    return {(arco.da_id, arco.a_id) for arco in archi}


def test_zona_di_quattro_eventi_genera_tre_sequenze():
    zona = _zona(0)
    sotto = _sotto(_evento(f"e{i}", zona.id, i) for i in range(4))

    collega_dorsale_eventi(sotto, [zona])

    dorsale = _dorsale(sotto)
    assert len(dorsale) == 3
    assert _coppie(dorsale) == {("e0", "e1"), ("e1", "e2"), ("e2", "e3")}
    for arco in dorsale:
        assert arco.tipo == "SEQUENZA"
        assert arco.props["base"] == BASE_DORSALE_ESPOSIZIONE
        assert arco.props["versione_regole"] == RULESET_VERSION
        assert arco.props["id"]
    # Content-addressed: one id per ordered pair, never a shared one.
    assert len({arco.props["id"] for arco in dorsale}) == 3


def test_catena_segue_posizione_chunk_non_ordine_di_inserimento():
    zona = _zona(0)
    sotto = _sotto(
        [
            _evento("e-terzo", zona.id, 2),
            _evento("e-primo", zona.id, 0),
            _evento("e-secondo", zona.id, 1),
        ]
    )

    collega_dorsale_eventi(sotto, [zona])

    assert _coppie(_dorsale(sotto)) == {
        ("e-primo", "e-secondo"),
        ("e-secondo", "e-terzo"),
    }


def test_sequenza_preesistente_non_viene_duplicata():
    zona = _zona(0)
    esistente = ArcoEvento(
        tipo="SEQUENZA",
        da_id="e0",
        a_id="e1",
        props={"regola": "sentence_pair_linking", "segnale": "poi"},
    )
    sotto = _sotto([_evento(f"e{i}", zona.id, i) for i in range(3)], [esistente])

    collega_dorsale_eventi(sotto, [zona])

    sequenze = [arco for arco in sotto.archi if arco.tipo == "SEQUENZA"]
    assert len(sequenze) == 2
    assert esistente in sotto.archi
    assert esistente.props["regola"] == "sentence_pair_linking"
    assert _coppie(_dorsale(sotto)) == {("e1", "e2")}


def test_precede_preesistente_non_blocca_la_dorsale():
    zona = _zona(0)
    precede = ArcoEvento(
        tipo="PRECEDE",
        da_id="e0",
        a_id="e1",
        props={"regola": "chiusura_temporale", "base": "connettivo"},
    )
    sotto = _sotto([_evento(f"e{i}", zona.id, i) for i in range(2)], [precede])

    collega_dorsale_eventi(sotto, [zona])

    assert _coppie(_dorsale(sotto)) == {("e0", "e1")}
    # Parallel assertions: the PRECEDE stays exactly as it was.
    assert precede in sotto.archi
    assert precede.props == {"regola": "chiusura_temporale", "base": "connettivo"}


def test_causa_preesistente_non_blocca_la_dorsale():
    zona = _zona(0)
    causa = ArcoEvento(
        tipo="CAUSA",
        da_id="e0",
        a_id="e1",
        props={"regola": "livello_relazioni"},
    )
    sotto = _sotto([_evento(f"e{i}", zona.id, i) for i in range(2)], [causa])

    collega_dorsale_eventi(sotto, [zona])

    assert _coppie(_dorsale(sotto)) == {("e0", "e1")}
    assert causa in sotto.archi


def test_nessuna_dorsale_fra_zone_diverse():
    z0, z1 = _zona(0), _zona(1)
    sotto = _sotto(
        [
            _evento("a0", z0.id, 0),
            _evento("a1", z0.id, 1),
            _evento("b0", z1.id, 0),
            _evento("b1", z1.id, 1),
        ]
    )

    collega_dorsale_eventi(sotto, [z0, z1])

    assert _coppie(_dorsale(sotto)) == {("a0", "a1"), ("b0", "b1")}


def test_eventi_fusi_esclusi_dalla_catena():
    zona = _zona(0)
    sotto = _sotto(
        [
            _evento("e0", zona.id, 0),
            _evento("e-fuso", zona.id, 1, fuso_in="e0"),
            _evento("e2", zona.id, 2),
        ]
    )

    collega_dorsale_eventi(sotto, [zona])

    dorsale = _dorsale(sotto)
    assert _coppie(dorsale) == {("e0", "e2")}
    assert all("e-fuso" not in (arco.da_id, arco.a_id) for arco in dorsale)


def test_zona_con_un_solo_evento_non_produce_archi():
    zona = _zona(0)
    sotto = _sotto([_evento("e0", zona.id, 0)])

    collega_dorsale_eventi(sotto, [zona])

    assert sotto.archi == []


def test_zona_senza_eventi_non_produce_archi():
    sotto = SottoGrafo()

    collega_dorsale_eventi(sotto, [_zona(0), _zona(1)])

    assert sotto.archi == []


def test_idempotenza_seconda_chiamata_non_aggiunge_nulla():
    zona = _zona(0)
    sotto = _sotto(_evento(f"e{i}", zona.id, i) for i in range(4))

    collega_dorsale_eventi(sotto, [zona])
    primo_giro = list(sotto.archi)
    ids_primo_giro = [arco.props["id"] for arco in primo_giro]

    collega_dorsale_eventi(sotto, [zona])

    assert sotto.archi == primo_giro
    assert [arco.props["id"] for arco in _dorsale(sotto)] == ids_primo_giro


def test_dorsale_e_persistibile():
    zona = _zona(0)
    sotto = _sotto(_evento(f"e{i}", zona.id, i) for i in range(3))

    collega_dorsale_eventi(sotto, [zona])

    # SEQUENZA is an event→event type: persisti() writes these arcs verbatim,
    # and _merge_arco keeps props["id"] as the relationship id.
    ammissibili = archi_ammissibili(sotto)
    assert len(ammissibili) == 2
    assert all(arco.props["id"] for arco in ammissibili)


def test_dorsale_agganciata_all_ingestione():
    source = PIPELINE_PATH.read_text(encoding="utf-8")
    assert "collega_dorsale_eventi" in pipeline.__all__
    # Both halves of the railway run once, after every zone is expanded.
    assert "collega_dorsale_eventi(sotto, zone)" in source
    assert "collega_dorsale_zone(sotto, zone)" in source
    assert source.index("collega_dorsale_eventi(sotto, zone)") < source.index(
        "genera_transizioni_zona(zone, job_id=job_id)"
    )
