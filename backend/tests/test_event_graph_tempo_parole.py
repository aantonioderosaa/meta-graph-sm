"""Spoken Italian/English dates and clocks occupy the same entities as digits."""

from __future__ import annotations

from app.pipeline.event_graph.tempo_iso import (
    _MESI_NUMERO,
    collocazione_da_espressione,
    orario_da_espressione,
)
from app.pipeline.event_graph.tempo_parole import (
    data_da_parole,
    intero_da_parole,
    orario_da_parole,
)


def test_intero_da_parole_anni_e_giorni():
    assert intero_da_parole("dodici") == 12
    assert intero_da_parole("ventotto") == 28
    assert intero_da_parole("millenovecentottantasette") == 1987
    assert intero_da_parole("millenovecentosessanta") == 1960
    assert intero_da_parole("duemilacinque") == 2005
    assert intero_da_parole("duemilaventiquattro") == 2024
    assert intero_da_parole("twelve") == 12
    assert intero_da_parole("nonunnumero") is None


def test_orario_da_parole_con_prefisso_e_frazioni():
    assert orario_da_parole("alle otto") == (8, 0, 0, "ora")
    assert orario_da_parole("alle otto e un quarto") == (8, 15, 0, "minuto")
    assert orario_da_parole("otto e mezza") == (8, 30, 0, "minuto")
    assert orario_da_parole("mezzogiorno") == (12, 0, 0, "minuto")
    assert orario_da_parole("mezzanotte") == (0, 0, 0, "minuto")
    assert orario_da_parole("all'una") == (1, 0, 0, "ora")
    assert orario_da_parole("otto") is None
    assert orario_da_espressione("alle otto") == (8, 0, 0)


def test_data_da_parole_mista_con_orario():
    assert data_da_parole(
        "dodici marzo millenovecentottantasette", _MESI_NUMERO
    ) == (12, 3, 1987)
    assert data_da_parole("primo maggio 1998", _MESI_NUMERO) == (1, 5, 1998)
    letto = collocazione_da_espressione(
        "dodici marzo millenovecentottantasette, alle otto e un quarto"
    )
    assert letto is not None
    assert letto.canonico == "1987-03-12T08:15"
