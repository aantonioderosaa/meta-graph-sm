"""Standalone temporal entities: date / clock / deadline / vague, never syntax."""

from __future__ import annotations

from app.pipeline.event_graph.tempo_entita import classifica_entita_temporale


def test_orario_data_scadenza_e_vaga_restano_entita():
    assert classifica_entita_temporale("ore 08:15").tipo == "ora"
    assert classifica_entita_temporale("alle 18").tipo == "ora"
    assert classifica_entita_temporale("18:30").tipo == "ora"
    assert classifica_entita_temporale("12 marzo 1987").tipo == "data"
    assert classifica_entita_temporale("nel 1843").tipo == "data"
    assert classifica_entita_temporale("nel 1843").forma == "1843"
    assert classifica_entita_temporale("1843-12-24").tipo == "data"
    assert classifica_entita_temporale("Dal 12 marzo 1987 al 15 marzo 1987").tipo == (
        "data"
    )
    assert classifica_entita_temporale("entro il 31 marzo 1987").tipo == "scadenza"
    assert classifica_entita_temporale("anni 70").tipo == "vaga"
    assert classifica_entita_temporale("dopo un po'").tipo == "vaga"
    assert classifica_entita_temporale("dopo un po").tipo == "vaga"
    assert classifica_entita_temporale("ieri").tipo == "vaga"
    assert classifica_entita_temporale("Natale").tipo == "vaga"
    assert classifica_entita_temporale("tra 20 minuti").tipo == "scadenza"
    assert classifica_entita_temporale("fra 20 minuti").tipo == "scadenza"
    assert classifica_entita_temporale("in 20 minutes").tipo == "scadenza"
    assert classifica_entita_temporale("entro 20 minuti").tipo == "scadenza"
    assert classifica_entita_temporale("10 giorni fa").tipo == "vaga"
    assert classifica_entita_temporale("20 minuti fa").tipo == "vaga"
    assert classifica_entita_temporale("2 hours ago").tipo == "vaga"


def test_date_e_orari_a_parole_sono_entita():
    assert classifica_entita_temporale("alle otto").tipo == "ora"
    assert classifica_entita_temporale("mezzogiorno").tipo == "ora"
    assert classifica_entita_temporale("all'una").tipo == "ora"
    assert classifica_entita_temporale("otto e mezza").tipo == "ora"
    assert (
        classifica_entita_temporale("dodici marzo millenovecentottantasette").tipo
        == "data"
    )
    assert classifica_entita_temporale("primo maggio 1998").tipo == "data"
    assert classifica_entita_temporale("dodici giorni fa").tipo == "vaga"


def test_sintassi_frasale_quantificata_non_e_entita():
    for span in (
        "dopo circa 3 ore",
        "Dopo circa 3 ore",
        "tre ore dopo",
        "3 hours later",
        "prima di due giorni",
        "circa 3 ore",
    ):
        assert classifica_entita_temporale(span).tipo is None, span
