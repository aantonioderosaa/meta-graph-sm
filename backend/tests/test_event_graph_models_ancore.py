"""MT1 — AncoraTemporale models: vocabs, defaults, lenient LLM coercion.

Pure model tests: no Neo4j, no LLM. Payloads are the structured-output schema
of a local model, so one dirty field must never reject the whole ancora.
"""

from __future__ import annotations

from typing import get_args

from app.models.event_graph import (
    GRANULARITA_TEMPORALI,
    NATURA_ANCORE,
    TIPI_ANCORA,
    AncoraTemporaleProposta,
    LivelloAncoreResult,
    NaturaAncora,
    PosizioneRispettoAncora,
    SegnaleAncoraEvento,
    TipoAncora,
    TipoRelazione,
)


def test_natura_e_tipo_ancora_vocabolari_chiusi():
    assert get_args(NaturaAncora) == ("esplicita", "intervallo", "aperta")
    assert NATURA_ANCORE == get_args(NaturaAncora)
    assert get_args(TipoAncora) == (
        "data",
        "ora",
        "scadenza",
        "epoca",
        "relativa",
        "simbolica",
    )
    assert TIPI_ANCORA == get_args(TipoAncora)
    assert get_args(PosizioneRispettoAncora) == ("prima", "durante", "dopo")


def test_successione_ancora_e_in_tipo_relazione():
    assert "SUCCESSIONE_ANCORA" in get_args(TipoRelazione)
    tipi = get_args(TipoRelazione)
    assert tipi.index("APPARTIENE_A") < tipi.index("SUCCESSIONE_ANCORA")
    assert "PRECEDE" not in tipi
    assert "CONTEMPORANEO" not in tipi
    assert "contemporaneo_a" not in SegnaleAncoraEvento.model_fields


def test_ancora_default_e_campi_nuovi():
    ancora = AncoraTemporaleProposta(etichetta="1843")
    assert ancora.natura == "esplicita"
    assert ancora.tipo == "simbolica"
    assert ancora.eventi == []
    assert ancora.descrizione is None
    assert ancora.granularita is None
    assert ancora.inizio is None
    assert ancora.fine is None
    assert ancora.espressione is None
    assert ancora.offset_inizio is None
    assert ancora.offset_fine is None
    assert ancora.stimato is False
    assert ancora.confidenza == 1.0
    assert ancora.posizione_doc_min is None
    assert ancora.padre is None


def test_chiave_ordine_non_e_un_campo_del_modello_proposto():
    assert "chiave_ordine" not in AncoraTemporaleProposta.model_fields
    ancora = AncoraTemporaleProposta.model_validate(
        {"etichetta": "1843", "tipo": "data", "chiave_ordine": 17}
    )
    assert not hasattr(ancora, "chiave_ordine")


def test_etichetta_tronca_a_40_caratteri_invece_di_rifiutare():
    corta = "24 dic, sera"
    assert AncoraTemporaleProposta(etichetta=corta).etichetta == corta
    esatta = "x" * 40
    assert AncoraTemporaleProposta(etichetta=esatta).etichetta == esatta
    lunga = "y" * 41
    assert AncoraTemporaleProposta(etichetta=lunga).etichetta == "y" * 40
    padre = "p" * 50
    ancora = AncoraTemporaleProposta(etichetta="sera", padre=padre)
    assert ancora.padre == "p" * 40


def test_confidenza_viene_clampata_invece_che_rifiutata():
    assert AncoraTemporaleProposta(etichetta="x", confidenza=1.7).confidenza == 1.0
    assert AncoraTemporaleProposta(etichetta="x", confidenza=-0.4).confidenza == 0.0
    assert AncoraTemporaleProposta(etichetta="x", confidenza=0.62).confidenza == 0.62
    assert SegnaleAncoraEvento(evento_id="e-0", confidenza=42).confidenza == 1.0
    assert SegnaleAncoraEvento(evento_id="e-0", confidenza=0.0).confidenza == 0.0


def test_confidenza_sporca_ricade_sul_default_senza_alzare():
    for sporca, atteso in (
        (None, 1.0),
        ("0.8", 0.8),
        ("0,8", 0.8),
        ("  0.5 ", 0.5),
        ("alta", 1.0),
        ([], 1.0),
        (float("nan"), 1.0),
        (float("inf"), 1.0),
    ):
        segnale = SegnaleAncoraEvento(evento_id="e-0", confidenza=sporca)
        assert segnale.confidenza == atteso, sporca


def test_natura_e_tipo_sporchi_vengono_coerciti():
    assert (
        AncoraTemporaleProposta(etichetta="x", natura="explicit").natura == "esplicita"
    )
    assert (
        AncoraTemporaleProposta(etichetta="x", natura="aperto").natura == "aperta"
    )
    assert AncoraTemporaleProposta(etichetta="x", natura="boh").natura == "esplicita"
    assert AncoraTemporaleProposta(etichetta="x", natura=7).natura == "esplicita"
    assert (
        AncoraTemporaleProposta(etichetta="x", tipo="data_esplicita").tipo == "data"
    )
    assert AncoraTemporaleProposta(etichetta="x", tipo="relativo").tipo == "relativa"
    assert AncoraTemporaleProposta(etichetta="x", tipo="simbolico").tipo == "simbolica"
    assert AncoraTemporaleProposta(etichetta="x", tipo="nope").tipo == "simbolica"


def test_granularita_sconosciuta_diventa_none_invece_di_rompere():
    assert AncoraTemporaleProposta(etichetta="x", granularita="epoca").granularita is None
    assert AncoraTemporaleProposta(etichetta="x", granularita=7).granularita is None
    assert (
        AncoraTemporaleProposta(etichetta="x", granularita="Giorni").granularita
        == "giorno"
    )


def test_testi_opzionali_vuoti_o_nulli_diventano_none():
    ancora = AncoraTemporaleProposta.model_validate(
        {
            "etichetta": "1843",
            "tipo": "data",
            "descrizione": "   ",
            "inizio": 1843,
            "fine": "null",
            "espressione": "n/a",
            "padre": "N/A",
        }
    )
    assert ancora.descrizione is None
    assert ancora.inizio == "1843"
    assert ancora.fine is None
    assert ancora.espressione is None
    assert ancora.padre is None
    assert SegnaleAncoraEvento(evento_id="e-0", ancora="").ancora is None
    assert SegnaleAncoraEvento(evento_id="e-0", base="").base is None


def test_offset_e_posizione_doc_sporchi_diventano_none():
    ancora = AncoraTemporaleProposta.model_validate(
        {
            "etichetta": "sera",
            "offset_inizio": "12",
            "offset_fine": "  18 ",
            "posizione_doc_min": "2",
        }
    )
    assert ancora.offset_inizio == 12
    assert ancora.offset_fine == 18
    assert ancora.posizione_doc_min == 2
    sporca = AncoraTemporaleProposta.model_validate(
        {
            "etichetta": "sera",
            "offset_inizio": "ciao",
            "offset_fine": -3,
            "posizione_doc_min": "null",
        }
    )
    assert sporca.offset_inizio is None
    assert sporca.offset_fine is None
    assert sporca.posizione_doc_min is None
    inf = AncoraTemporaleProposta(etichetta="sera", offset_inizio=float("inf"))
    assert inf.offset_inizio is None


def test_stimato_accetta_le_forme_sporche_del_modello():
    assert SegnaleAncoraEvento(evento_id="e-0", stimato=None).stimato is False
    assert SegnaleAncoraEvento(evento_id="e-0", stimato="vero").stimato is True
    assert SegnaleAncoraEvento(evento_id="e-0", stimato="falso").stimato is False
    assert SegnaleAncoraEvento(evento_id="e-0", stimato="true").stimato is True


def test_posizione_prima_durante_dopo_viene_coercita():
    assert SegnaleAncoraEvento(evento_id="e-0").posizione is None
    assert (
        SegnaleAncoraEvento(evento_id="e-0", posizione="before").posizione == "prima"
    )
    assert (
        SegnaleAncoraEvento(evento_id="e-0", posizione="DURING").posizione
        == "durante"
    )
    assert SegnaleAncoraEvento(evento_id="e-0", posizione="boh").posizione is None


def test_round_trip_da_dict_sporco_stile_output_llm():
    grezzo = {
        "segnali": [
            {
                "evento_id": "e-0",
                "ancora": "24 dic, sera",
                "posizione": "durante",
                "stimato": "false",
                "confidenza": "0,95",
                "base": None,
            },
            {
                "evento_id": "e-1",
                "ancora": "24 dic",
                "posizione": "prima",
                "stimato": True,
                "confidenza": 1.4,
                "base": "e-0",
            },
        ],
        "ancore": [
            {
                "etichetta": "24 dic 1843",
                "natura": "esplicita",
                "tipo": "data",
                "eventi": ["e-0"],
                "descrizione": "La vigilia in casa Cratchit",
                "granularita": "GIORNO",
                "inizio": "1843-12-24",
                "fine": "  ",
                "espressione": "24 dicembre",
                "offset_inizio": 10,
                "offset_fine": 21,
                "stimato": "no",
                "confidenza": 0.8,
                "padre": "1843",
            },
            {
                "etichetta": "1843",
                "natura": "esplicita",
                "tipo": "data",
                "granularita": "anno",
                "inizio": "1843",
            },
        ],
    }
    result = LivelloAncoreResult.model_validate(grezzo)

    primo, secondo = result.segnali
    assert primo.posizione == "durante"
    assert primo.stimato is False
    assert primo.confidenza == 0.95
    assert secondo.posizione == "prima"
    assert secondo.stimato is True
    assert secondo.confidenza == 1.0
    assert secondo.base == "e-0"

    figlio, radice = result.ancore
    assert figlio.granularita == "giorno"
    assert figlio.fine is None
    assert figlio.stimato is False
    assert figlio.padre == "1843"
    assert figlio.espressione == "24 dicembre"
    assert radice.eventi == []
    assert radice.padre is None

    assert LivelloAncoreResult.model_validate(result.model_dump()) == result


def test_livello_ancore_result_resta_vuoto_di_default():
    vuoto = LivelloAncoreResult()
    assert vuoto.segnali == []
    assert vuoto.ancore == []


def test_schema_json_espone_vocabolari_ancora():
    schema = LivelloAncoreResult.model_json_schema()
    definizioni = schema.get("$defs", {})
    ancora = definizioni["AncoraTemporaleProposta"]["properties"]
    assert set(ancora) == {
        "etichetta",
        "natura",
        "tipo",
        "eventi",
        "descrizione",
        "granularita",
        "inizio",
        "fine",
        "espressione",
        "offset_inizio",
        "offset_fine",
        "stimato",
        "confidenza",
        "posizione_doc_min",
        "padre",
    }
    assert "chiave_ordine" not in ancora
    blob = str(schema)
    for valore in NATURA_ANCORE + TIPI_ANCORA + GRANULARITA_TEMPORALI:
        assert f"'{valore}'" in blob
    assert "'prima'" in blob
    assert "'durante'" in blob
    assert "'dopo'" in blob
