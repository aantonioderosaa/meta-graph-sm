"""MT1 / livello temporale v2 — models for nested clusters and placements.

Pure model tests: no Neo4j, no LLM. The two payloads are the structured-output
schema of a local model, so the tests pin both the contract (defaults,
granularity vocabulary, backward compatibility) and the tolerance to slightly
dirty output.
"""

from __future__ import annotations

from typing import get_args

from app.models.event_graph import (
    GRANULARITA_TEMPORALI,
    ClusterTemporaleProposto,
    GranularitaTemporale,
    LivelloTemporaleResult,
    SegnaleTemporaleEvento,
)

GRANULARITA_ATTESE = (
    "secondo",
    "minuto",
    "ora",
    "giorno",
    "settimana",
    "mese",
    "stagione",
    "anno",
    "decennio",
    "secolo",
)


def test_granularita_ha_i_dieci_valori_dal_piu_fine_al_piu_grosso():
    assert get_args(GranularitaTemporale) == GRANULARITA_ATTESE
    assert GRANULARITA_TEMPORALI == GRANULARITA_ATTESE
    assert len(set(GRANULARITA_TEMPORALI)) == 10
    assert GRANULARITA_TEMPORALI.index("secondo") < GRANULARITA_TEMPORALI.index(
        "giorno"
    )
    assert GRANULARITA_TEMPORALI.index("giorno") < GRANULARITA_TEMPORALI.index(
        "anno"
    )
    assert GRANULARITA_TEMPORALI.index("anno") < GRANULARITA_TEMPORALI.index(
        "secolo"
    )


def test_segnale_vecchio_stile_resta_valido_e_i_nuovi_campi_hanno_default():
    segnale = SegnaleTemporaleEvento(
        evento_id="e-0",
        tempo_assoluto="1990",
        espressione_relativa="il giorno dopo",
        contemporaneo_a=["e-1"],
    )
    assert segnale.granularita is None
    assert segnale.stimato is False
    assert segnale.confidenza == 1.0
    assert segnale.base is None
    assert segnale.precede == []


def test_cluster_vecchio_stile_resta_valido_e_i_nuovi_campi_hanno_default():
    cluster = ClusterTemporaleProposto(
        etichetta="1994",
        tipo="data_esplicita",
        eventi=["e-0", "e-1"],
    )
    assert cluster.descrizione is None
    assert cluster.granularita is None
    assert cluster.inizio is None
    assert cluster.fine is None
    assert cluster.stimato is False
    assert cluster.confidenza == 1.0
    assert cluster.padre is None


def test_cluster_puo_contenere_solo_altri_cluster_senza_eventi():
    padre = ClusterTemporaleProposto(
        etichetta="24 dic 1843",
        tipo="intervallo",
        granularita="giorno",
        inizio="1843-12-24",
    )
    figlio = ClusterTemporaleProposto(
        etichetta="24 dic, sera",
        tipo="data_esplicita",
        granularita="ora",
        inizio="1843-12-24T18",
        eventi=["e-0"],
        padre="24 dic 1843",
    )
    assert padre.eventi == []
    assert figlio.padre == padre.etichetta


def test_chiave_ordine_non_e_un_campo_del_modello_proposto():
    assert "chiave_ordine" not in ClusterTemporaleProposto.model_fields
    cluster = ClusterTemporaleProposto.model_validate(
        {"etichetta": "1843", "tipo": "data_esplicita", "chiave_ordine": 17}
    )
    assert not hasattr(cluster, "chiave_ordine")


def test_confidenza_viene_clampata_invece_che_rifiutata():
    assert ClusterTemporaleProposto(
        etichetta="x", tipo="simbolico", confidenza=1.7
    ).confidenza == 1.0
    assert ClusterTemporaleProposto(
        etichetta="x", tipo="simbolico", confidenza=-0.4
    ).confidenza == 0.0
    assert ClusterTemporaleProposto(
        etichetta="x", tipo="simbolico", confidenza=0.62
    ).confidenza == 0.62
    assert SegnaleTemporaleEvento(evento_id="e-0", confidenza=42).confidenza == 1.0
    assert SegnaleTemporaleEvento(evento_id="e-0", confidenza=0.0).confidenza == 0.0


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
        segnale = SegnaleTemporaleEvento(evento_id="e-0", confidenza=sporca)
        assert segnale.confidenza == atteso, sporca


def test_granularita_sconosciuta_diventa_none_invece_di_rompere():
    assert (
        ClusterTemporaleProposto(
            etichetta="x", tipo="simbolico", granularita="epoca"
        ).granularita
        is None
    )
    assert (
        ClusterTemporaleProposto(
            etichetta="x", tipo="simbolico", granularita=7
        ).granularita
        is None
    )
    assert (
        ClusterTemporaleProposto(
            etichetta="x", tipo="simbolico", granularita="Giorni"
        ).granularita
        == "giorno"
    )
    assert (
        SegnaleTemporaleEvento(evento_id="e-0", granularita="YEAR").granularita
        == "anno"
    )


def test_testi_opzionali_vuoti_o_nulli_diventano_none():
    cluster = ClusterTemporaleProposto.model_validate(
        {
            "etichetta": "1843",
            "tipo": "data_esplicita",
            "descrizione": "   ",
            "inizio": 1843,
            "fine": "null",
            "padre": "N/A",
        }
    )
    assert cluster.descrizione is None
    assert cluster.inizio == "1843"
    assert cluster.fine is None
    assert cluster.padre is None
    assert SegnaleTemporaleEvento(evento_id="e-0", base="").base is None


def test_base_tiene_id_ancora_o_motivazione_libera():
    da_evento = SegnaleTemporaleEvento(
        evento_id="e-2", stimato=True, confidenza=0.7, base="e-1"
    )
    da_motivazione = SegnaleTemporaleEvento(
        evento_id="e-3",
        stimato=True,
        confidenza=0.4,
        base="subito dopo la cena di Natale",
    )
    assert da_evento.base == "e-1"
    assert da_motivazione.base.startswith("subito dopo")


def test_stimato_accetta_le_forme_sporche_del_modello():
    assert SegnaleTemporaleEvento(evento_id="e-0", stimato=None).stimato is False
    assert SegnaleTemporaleEvento(evento_id="e-0", stimato="vero").stimato is True
    assert SegnaleTemporaleEvento(evento_id="e-0", stimato="falso").stimato is False
    assert SegnaleTemporaleEvento(evento_id="e-0", stimato="true").stimato is True


def test_round_trip_da_dict_sporco_stile_output_llm():
    grezzo = {
        "segnali": [
            {
                "evento_id": "e-0",
                "tempo_assoluto": "1843-12-24T18:30",
                "espressione_relativa": None,
                "contemporaneo_a": [],
                "granularita": "minuti",
                "stimato": "false",
                "confidenza": "0,95",
                "base": None,
            },
            {
                "evento_id": "e-1",
                "granularita": "boh",
                "stimato": True,
                "confidenza": 1.4,
                "base": "e-0",
            },
        ],
        "cluster": [
            {
                "etichetta": "24 dic 1843",
                "tipo": "intervallo",
                "eventi": ["e-0", "e-1"],
                "descrizione": "La vigilia in casa Cratchit",
                "granularita": "GIORNO",
                "inizio": "1843-12-24",
                "fine": "  ",
                "stimato": "no",
                "confidenza": 0.8,
                "padre": "1843",
            },
            {
                "etichetta": "1843",
                "tipo": "intervallo",
                "granularita": "anno",
                "inizio": "1843",
            },
        ],
    }
    result = LivelloTemporaleResult.model_validate(grezzo)

    primo, secondo = result.segnali
    assert primo.granularita == "minuto"
    assert primo.stimato is False
    assert primo.confidenza == 0.95
    assert secondo.granularita is None
    assert secondo.stimato is True
    assert secondo.confidenza == 1.0
    assert secondo.base == "e-0"

    figlio, radice = result.cluster
    assert figlio.granularita == "giorno"
    assert figlio.fine is None
    assert figlio.stimato is False
    assert figlio.padre == "1843"
    assert radice.eventi == []
    assert radice.padre is None

    assert LivelloTemporaleResult.model_validate(
        result.model_dump()
    ) == result


def test_livello_temporale_result_resta_vuoto_di_default():
    vuoto = LivelloTemporaleResult()
    assert vuoto.segnali == []
    assert vuoto.cluster == []


def test_schema_json_espone_le_dieci_granularita_al_modello():
    schema = LivelloTemporaleResult.model_json_schema()
    definizioni = schema.get("$defs", {})
    cluster = definizioni["ClusterTemporaleProposto"]["properties"]
    assert set(cluster) == {
        "etichetta",
        "tipo",
        "eventi",
        "descrizione",
        "granularita",
        "inizio",
        "fine",
        "stimato",
        "confidenza",
        "padre",
    }
    blob = str(schema)
    for valore in GRANULARITA_ATTESE:
        assert f"'{valore}'" in blob
