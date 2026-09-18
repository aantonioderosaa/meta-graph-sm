"""MT2 / livello temporale v2 — tempo ISO a precisione variabile.

Test tabellari puri: nessun Neo4j, nessun LLM. Coprono le sei precisioni ISO
per le dieci granularità, la monotonia di `chiave_ordine`, la coerenza fra
`bounds` e chiave, il degrado silenzioso sugli input sporchi, e l'estensione di
`temporal_placement._expand_bounds` alle ore / minuti / secondi — con
asserzioni sui valori *esatti* di anno / mese / giorno, che il piano vieta di
cambiare.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models.event_graph import GRANULARITA_TEMPORALI, EventoRisolto
from app.pipeline.event_graph.chiusura_temporale import _allen_from_assoluto
from app.pipeline.event_graph.tempo_iso import (
    PRECISIONI_ISO,
    SCALA_CHIAVE,
    analizza,
    bounds,
    chiave_ordine,
    collocazione_da_espressione,
    intervallo_da_espressione,
    normalizza,
    orario_da_espressione,
    piu_grossa,
    precisione,
    rango_granularita,
)
from app.pipeline.event_graph.temporal_placement import _expand_bounds

# Una collocazione per ciascuna delle sei precisioni ISO, tutte annidate nella
# stessa istante-radice, così che le si possa anche confrontare fra loro.
PRECISIONI = (
    ("1843", "anno", (1843, 1, 1, 0, 0, 0)),
    ("1843-12", "mese", (1843, 12, 1, 0, 0, 0)),
    ("1843-12-24", "giorno", (1843, 12, 24, 0, 0, 0)),
    ("1843-12-24T18", "ora", (1843, 12, 24, 18, 0, 0)),
    ("1843-12-24T18:30", "minuto", (1843, 12, 24, 18, 30, 0)),
    ("1843-12-24T18:30:15", "secondo", (1843, 12, 24, 18, 30, 15)),
)

MALFORMATI = (
    None,
    "",
    "   ",
    "ieri",
    "18:30",
    "1843-13-45",
    "1843-13",
    "1843-02-30",
    "1843-12-24T25:00",
    "1843-12-24T18:60",
    "0000",
    "0000-01-01",
    "843",
    "18430",
    "1843-1-1",
    43,
    1843,
    3.14,
    True,
    ["1843"],
    {"da": "1843", "a": "1844"},
)


# --- parsing e precisione ----------------------------------------------------


@pytest.mark.parametrize("testo, attesa, parti", PRECISIONI)
def test_analizza_riconosce_le_sei_precisioni_iso(testo, attesa, parti):
    tempo = analizza(testo)
    assert tempo is not None
    assert tempo.precisione == attesa
    assert (
        tempo.anno,
        tempo.mese,
        tempo.giorno,
        tempo.ora,
        tempo.minuto,
        tempo.secondo,
    ) == parti
    # L'inizio non è "un momento imprecisato dell'unità": è il suo primo istante.
    assert tempo.canonico == testo
    assert precisione(testo) == attesa


@pytest.mark.parametrize("testo, _attesa, parti", PRECISIONI)
def test_la_scala_e_secondi_dallanno_uno_per_sedici(testo, _attesa, parti):
    """Pin della scala: sedicesimi di secondo dal 0001-01-01T00:00:00."""
    tempo = analizza(testo)
    atteso = (datetime(*parti) - datetime(1, 1, 1)).total_seconds()
    assert tempo.secondi == int(atteso)
    assert bounds(testo)[0] == tempo.secondi * SCALA_CHIAVE


def test_le_sei_precisioni_del_modulo_sono_quelle_dichiarate():
    assert PRECISIONI_ISO == ("anno", "mese", "giorno", "ora", "minuto", "secondo")
    assert tuple(attesa for _, attesa, _ in PRECISIONI) == PRECISIONI_ISO
    # Sono tutte granularità legittime dell'enum condiviso con i modelli.
    assert set(PRECISIONI_ISO) <= set(GRANULARITA_TEMPORALI)


@pytest.mark.parametrize(
    "testo, canonico, attesa",
    [
        ("1843-12-24 18:30", "1843-12-24T18:30", "minuto"),
        ("1843-12-24t18:30", "1843-12-24T18:30", "minuto"),
        (" 1843-12-24T18:30Z", "1843-12-24T18:30", "minuto"),
        ("1843-12-24T18:30+01:00", "1843-12-24T18:30", "minuto"),
        ("1843-12-24T18:30:15.500", "1843-12-24T18:30:15", "secondo"),
        ("1843-12-24T18Z", "1843-12-24T18", "ora"),
    ],
)
def test_analizza_tollera_separatori_fusi_e_frazioni(testo, canonico, attesa):
    """Fuso e frazioni sono accettati e scartati: tempo narrativo, non fisico."""
    tempo = analizza(testo)
    assert tempo is not None
    assert tempo.canonico == canonico
    assert tempo.precisione == attesa


@pytest.mark.parametrize("valore", MALFORMATI)
def test_input_malformati_degradano_senza_eccezione(valore):
    assert analizza(valore) is None
    assert precisione(valore) is None
    assert chiave_ordine(valore) is None
    assert bounds(valore) is None
    assert normalizza(valore) is None
    # Anche con una granularità dichiarata valida: la stringa resta il vincolo.
    assert chiave_ordine(valore, "giorno") is None
    assert bounds(valore, "giorno") is None


@pytest.mark.parametrize("granularita", GRANULARITA_TEMPORALI)
def test_granularita_sporca_o_assente_non_annulla_la_collocazione(granularita):
    """Una granularità illeggibile fa ricadere sulla precisione della stringa."""
    atteso = chiave_ordine("1843-12-24", None)
    assert atteso is not None
    for sporca in (None, "", "boh", 7, ["giorno"]):
        assert chiave_ordine("1843-12-24", sporca) == atteso
        assert bounds("1843-12-24", sporca) == bounds("1843-12-24", "giorno")
    assert chiave_ordine("1843-12-24", granularita) is not None


# --- chiave_ordine -----------------------------------------------------------


@pytest.mark.parametrize("granularita", GRANULARITA_TEMPORALI)
@pytest.mark.parametrize("testo, _attesa, _parti", PRECISIONI)
def test_chiave_ordine_esiste_per_ogni_coppia_precisione_granularita(
    testo, _attesa, _parti, granularita
):
    chiave = chiave_ordine(testo, granularita)
    assert isinstance(chiave, int)
    assert chiave > 0


def test_anno_nudo_viene_prima_del_minuto_dentro_lo_stesso_anno():
    """Il caso nominato dal piano: 1843 contro 1843-12-24T18:30."""
    assert chiave_ordine("1843") < chiave_ordine("1843-12-24T18:30")
    # E vale anche dichiarando le granularità, in entrambi i sensi.
    assert chiave_ordine("1843", "anno") < chiave_ordine(
        "1843-12-24T18:30", "minuto"
    )
    assert chiave_ordine("1843", "secondo") < chiave_ordine(
        "1843-12-24T18:30", "secolo"
    )


def test_ordinamento_misto_di_precisioni_e_granularita_diverse():
    mescolato = [
        ("1843-12-24T18:30:15", "secondo"),
        ("1900", "secolo"),
        ("1843-12", "mese"),
        ("1843-12-24T18:30", "minuto"),
        ("1843", "anno"),
        ("1850-06-15", "giorno"),
        ("1843-12-24", "giorno"),
        ("1843-12-24T18", "ora"),
        ("1843-12-24T18:30:15", "minuto"),
    ]
    ordinato = sorted(mescolato, key=lambda coppia: chiave_ordine(*coppia))
    assert ordinato == [
        ("1843", "anno"),
        ("1843-12", "mese"),
        ("1843-12-24", "giorno"),
        ("1843-12-24T18", "ora"),
        ("1843-12-24T18:30", "minuto"),
        # Stesso istante, granularità diversa: la più grossa ordina prima.
        ("1843-12-24T18:30:15", "minuto"),
        ("1843-12-24T18:30:15", "secondo"),
        ("1850-06-15", "giorno"),
        ("1900", "secolo"),
    ]


def test_chiave_ordine_e_monotona_rispetto_allistante_di_inizio():
    cronologico = [testo for testo, _, _ in PRECISIONI] + [
        "1843-12-24T18:30:16",
        "1843-12-25",
        "1844",
        "1900-01",
        "2026-09-10T05:56:00",
    ]
    chiavi = [chiave_ordine(testo) for testo in cronologico]
    assert chiavi == sorted(chiavi)
    istanti = [
        (
            tempo.anno,
            tempo.mese,
            tempo.giorno,
            tempo.ora,
            tempo.minuto,
            tempo.secondo,
        )
        for tempo in (analizza(testo) for testo in cronologico)
    ]
    assert istanti == sorted(istanti)


def test_a_pari_istante_la_granularita_piu_grossa_ordina_prima():
    """Il potenziale padre precede il potenziale figlio, in modo deterministico."""
    chiavi = [
        chiave_ordine("1843-01-01T00:00:00", granularita)
        for granularita in reversed(GRANULARITA_TEMPORALI)
    ]
    assert chiavi == sorted(chiavi)
    assert len(set(chiavi)) == len(GRANULARITA_TEMPORALI)
    # Il tie-break vive nei bit bassi: non sposta mai l'istante di un secondo.
    assert max(chiavi) - min(chiavi) < SCALA_CHIAVE


def test_il_tiebreak_non_scavalca_mai_un_istante_successivo():
    grossa = chiave_ordine("1843-12-24T18:30:15", "secolo")
    fine_successivo = chiave_ordine("1843-12-24T18:30:16", "secondo")
    assert grossa < fine_successivo


# --- bounds ------------------------------------------------------------------


@pytest.mark.parametrize("granularita", GRANULARITA_TEMPORALI)
def test_bounds_contiene_la_chiave_per_ogni_granularita(granularita):
    for testo, _, _ in PRECISIONI:
        intervallo = bounds(testo, granularita)
        chiave = chiave_ordine(testo, granularita)
        assert intervallo is not None
        inizio, fine = intervallo
        assert inizio < fine
        assert inizio <= chiave < fine


def test_bounds_e_chiave_ordinano_allo_stesso_modo():
    campione = [
        ("1843", "anno"),
        ("1843-12", "mese"),
        ("1843-12-24", "giorno"),
        ("1843-12-24T18", "ora"),
        ("1843-12-24T18:30", "minuto"),
        ("1843-12-24T18:30:15", "secondo"),
        ("1850-06-15", "settimana"),
        ("1900", "secolo"),
    ]
    per_chiave = sorted(campione, key=lambda coppia: chiave_ordine(*coppia))
    inizi = [bounds(*coppia)[0] for coppia in per_chiave]
    assert inizi == sorted(inizi)
    # L'inizio dei bounds è la chiave al netto del tie-break.
    for coppia in campione:
        assert bounds(*coppia)[0] <= chiave_ordine(*coppia)
        assert chiave_ordine(*coppia) - bounds(*coppia)[0] < SCALA_CHIAVE


@pytest.mark.parametrize(
    "granularita, giorni, secondi",
    [
        ("secondo", 0, 1),
        ("minuto", 0, 60),
        ("ora", 0, 3_600),
        ("giorno", 1, 0),
        ("settimana", 7, 0),
        # Le granularità di calendario sono ancorate al 24 dicembre 1843 e
        # contano i giorni reali: un mese è il dicembre che segue, un anno
        # arriva al 24 dicembre 1844 passando per il febbraio bisestile.
        ("mese", 31, 0),
        ("stagione", 31 + 31 + 29, 0),
        ("anno", 366, 0),
        ("decennio", 3_653, 0),
        ("secolo", 36_524, 0),
    ],
)
def test_ampiezza_dei_bounds_per_granularita(granularita, giorni, secondi):
    inizio, fine = bounds("1843-12-24T18:30:15", granularita)
    assert fine - inizio == (giorni * 86_400 + secondi) * SCALA_CHIAVE


@pytest.mark.parametrize("granularita", ["secondo", "minuto", "ora"])
def test_una_data_al_giorno_non_si_restringe_sotto_il_giorno(granularita):
    """Non si inventa l'ora che la stringa non dà: l'ampiezza resta il giorno."""
    assert bounds("1843-12-24", granularita) == bounds("1843-12-24", "giorno")


def test_ampiezza_cresce_con_la_granularita():
    ampiezze = []
    for granularita in GRANULARITA_TEMPORALI:
        inizio, fine = bounds("1843-06-15T12:30:30", granularita)
        ampiezze.append(fine - inizio)
    assert ampiezze == sorted(ampiezze)
    assert len(set(ampiezze)) == len(GRANULARITA_TEMPORALI)


def test_bounds_sono_semiaperti_e_le_unita_adiacenti_si_toccano():
    """`fine` esclusiva: due unità contigue danno `a_fine == b_inizio` (Allen meets)."""
    _, fine_dicembre = bounds("1843-12", "mese")
    inizio_gennaio, _ = bounds("1844-01", "mese")
    assert fine_dicembre == inizio_gennaio

    _, fine_minuto = bounds("1843-12-24T18:30", "minuto")
    inizio_successivo, _ = bounds("1843-12-24T18:31", "minuto")
    assert fine_minuto == inizio_successivo


def test_bounds_annidati_quando_la_granularita_e_coerente():
    anno = bounds("1843", "anno")
    mese = bounds("1843-12", "mese")
    giorno = bounds("1843-12-24", "giorno")
    minuto = bounds("1843-12-24T18:30", "minuto")
    for interno, esterno in ((mese, anno), (giorno, mese), (minuto, giorno)):
        assert esterno[0] <= interno[0]
        assert interno[1] <= esterno[1]


def test_la_stringa_fissa_linizio_e_la_granularita_piu_grossa_lampiezza():
    """`("1843", "giorno")`: il giorno non lo sappiamo, l'intervallo resta l'anno."""
    assert bounds("1843", "giorno") == bounds("1843", "anno")
    assert bounds("1843", "secondo") == bounds("1843", "anno")
    # Inizio sempre dalla stringa, anche quando la dichiarazione è più grossa.
    dichiarata_grossa = bounds("1843-12-24T18:30", "anno")
    assert dichiarata_grossa[0] == bounds("1843-12-24T18:30", "minuto")[0]
    assert dichiarata_grossa[1] > bounds("1843-12-24T18:30", "minuto")[1]
    assert chiave_ordine("1843-12-24T18:30", "anno") > chiave_ordine("1843", "anno")


def test_stagione_e_un_trimestre_ancorato_allinizio_dichiarato():
    """Nessun ISO per le stagioni: trimestre dall'inizio, non date fisse boreali."""
    inverno = bounds("1843-12", "stagione")
    assert inverno[0] == bounds("1843-12", "mese")[0]
    # dicembre, gennaio, febbraio 1844 (bisestile)
    assert inverno[1] - inverno[0] == (31 + 31 + 29) * 86_400 * SCALA_CHIAVE
    # L'ancoraggio è la stringa, quindi funziona anche a rovescio (emisfero sud).
    estate_sud = bounds("1843-06", "stagione")
    assert estate_sud[0] == bounds("1843-06", "mese")[0]
    assert estate_sud[1] - estate_sud[0] == (30 + 31 + 31) * 86_400 * SCALA_CHIAVE


def test_bounds_tagliati_al_limite_dellasse_rappresentabile():
    """Fine oltre il 9999 tagliata, senza eccezioni e senza invertire l'intervallo."""
    for granularita in ("secolo", "decennio", "anno", "mese"):
        inizio, fine = bounds("9999-12-31T23:59:59", granularita)
        assert inizio < fine


# --- granularità -------------------------------------------------------------


def test_rango_granularita_ordina_dal_fine_al_grosso():
    ranghi = [rango_granularita(g) for g in GRANULARITA_TEMPORALI]
    assert ranghi == list(range(len(GRANULARITA_TEMPORALI)))
    assert rango_granularita("SECONDO") == 0
    assert rango_granularita("  anno ") == GRANULARITA_TEMPORALI.index("anno")
    for sporca in (None, "", "boh", "gg", 3, ["anno"]):
        assert rango_granularita(sporca) is None


@pytest.mark.parametrize(
    "prima, seconda, attesa",
    [
        ("secondo", "anno", "anno"),
        ("anno", "secondo", "anno"),
        ("giorno", "giorno", "giorno"),
        ("mese", "stagione", "stagione"),
        ("decennio", "secolo", "secolo"),
        ("giorno", None, "giorno"),
        (None, "giorno", "giorno"),
        ("giorno", "boh", "giorno"),
        (None, None, None),
        ("boh", 7, None),
    ],
)
def test_piu_grossa(prima, seconda, attesa):
    assert piu_grossa(prima, seconda) == attesa


# --- normalizza (chiave di identità per MT4) ---------------------------------


def test_normalizza_da_una_chiave_di_identita_stabile():
    assert normalizza("1843-12-24T18:30", "minuto") == ("1843-12-24T18:30", "minuto")
    # Riformulazioni della stessa collocazione collassano sulla stessa chiave.
    varianti = [
        "1843-12-24T18:30",
        " 1843-12-24T18:30 ",
        "1843-12-24 18:30",
        "1843-12-24t18:30",
        "1843-12-24T18:30Z",
        "1843-12-24T18:30+02:00",
    ]
    assert len({normalizza(testo, "minuto") for testo in varianti}) == 1


def test_normalizza_non_fonde_collocazioni_diverse():
    assert normalizza("1843", "anno") != normalizza("1843-12", "anno")
    assert normalizza("1843-12-24", "giorno") != normalizza("1843-12-25", "giorno")
    # La granularità effettiva è la più grossa fra stringa e dichiarazione.
    assert normalizza("1843", "giorno") == ("1843", "anno")
    assert normalizza("1843-12-24T18:30", "anno") == ("1843-12-24T18:30", "anno")


# --- _expand_bounds: estensione senza regressioni ----------------------------


@pytest.mark.parametrize(
    "testo, inizio, fine",
    [
        # I tre casi che il piano vieta di cambiare, con i valori esatti di prima.
        ("1843", datetime(1843, 1, 1), datetime(1843, 12, 31, 23, 59, 59)),
        ("1843-12", datetime(1843, 12, 1), datetime(1843, 12, 31, 23, 59, 59)),
        ("1843-02", datetime(1843, 2, 1), datetime(1843, 2, 28, 23, 59, 59)),
        ("1844-02", datetime(1844, 2, 1), datetime(1844, 2, 29, 23, 59, 59)),
        (
            "1843-12-24",
            datetime(1843, 12, 24),
            datetime(1843, 12, 24, 23, 59, 59),
        ),
        ("1990", datetime(1990, 1, 1), datetime(1990, 12, 31, 23, 59, 59)),
        ("1994-04", datetime(1994, 4, 1), datetime(1994, 4, 30, 23, 59, 59)),
        (
            "1994-04-06",
            datetime(1994, 4, 6),
            datetime(1994, 4, 6, 23, 59, 59),
        ),
    ],
)
def test_expand_bounds_su_anno_mese_giorno_non_cambia(testo, inizio, fine):
    assert _expand_bounds(testo) == (inizio, fine)


@pytest.mark.parametrize(
    "testo, inizio, fine",
    [
        (
            "1843-12-24T18",
            datetime(1843, 12, 24, 18, 0, 0),
            datetime(1843, 12, 24, 18, 59, 59),
        ),
        (
            "1843-12-24T18:30",
            datetime(1843, 12, 24, 18, 30, 0),
            datetime(1843, 12, 24, 18, 30, 59),
        ),
        (
            "1843-12-24T18:30:15",
            datetime(1843, 12, 24, 18, 30, 15),
            datetime(1843, 12, 24, 18, 30, 15),
        ),
    ],
)
def test_expand_bounds_copre_ore_minuti_secondi(testo, inizio, fine):
    """`T18` prima non era riconosciuto; minuti e ore ora chiudono la loro unità."""
    assert _expand_bounds(testo) == (inizio, fine)


def test_expand_bounds_secondo_resta_larghezza_zero():
    """Il secondo è l'unità atomica: inizio e fine coincidono, come prima."""
    inizio, fine = _expand_bounds("1994-04-06T12:00:00")
    assert inizio == fine == datetime(1994, 4, 6, 12, 0, 0)


def test_expand_bounds_intervallo_esplicito_usa_le_nuove_precisioni():
    assert _expand_bounds({"da": "1843", "a": "1843-12-24T18"}) == (
        datetime(1843, 1, 1),
        datetime(1843, 12, 24, 18, 59, 59),
    )


@pytest.mark.parametrize(
    "valore",
    [None, "", "   ", "ieri", "18:30", "1843-13-45", "1843-13", "0000", 43],
)
def test_expand_bounds_degrada_senza_eccezione(valore):
    assert _expand_bounds(valore) is None


# --- coerenza con la rete di Allen -------------------------------------------


def _evento(evento_id: str, tempo: str) -> EventoRisolto:
    return EventoRisolto(id=evento_id, lemma="accadere", tempo_assoluto=tempo)


@pytest.mark.parametrize(
    "sinistra, destra, attesa",
    [
        # Le relazioni su anno / mese / giorno non si muovono.
        ("1990", "1992", "before"),
        ("1992", "1990", "after"),
        ("1994", "1994", "equals"),
        ("1994-04", "1994-05", "before"),
        ("1994-04-06", "1994-04-07", "before"),
        ("1994-01", "1994", "starts"),
        ("1994-12", "1994", "finishes"),
        ("1994", "1994-06", "contains"),
        ("1994-04", "1994", "during"),
        # Le tre precisioni nuove si inseriscono senza casi degeneri: chiudere
        # l'unità invece di restare a larghezza zero è ciò che rende
        # riconoscibili starts / finishes anche sotto il giorno.
        ("1843-12-24T18", "1843-12-24T19", "before"),
        ("1843-12-24T18:30", "1843-12-24T18:31", "before"),
        ("1843-12-24T18:30", "1843-12-24T18:30", "equals"),
        ("1843-12-24T18:00", "1843-12-24T18", "starts"),
        ("1843-12-24T18:59", "1843-12-24T18", "finishes"),
        ("1843-12-24T18:30", "1843-12-24T18", "during"),
        ("1843-12-24T18", "1843-12-24", "during"),
        ("1843-12-24T18", "1843-12-24T18:30", "contains"),
    ],
)
def test_allen_da_tempo_assoluto_resta_coerente(sinistra, destra, attesa):
    relazioni = _allen_from_assoluto(_evento("a", sinistra), _evento("b", destra))
    assert relazioni == {attesa}


def test_allen_ignora_le_collocazioni_illeggibili():
    assert _allen_from_assoluto(_evento("a", "ieri"), _evento("b", "1843")) is None


def test_collocazione_da_espressione_legge_date_italiane_senza_cambiare_analizza():
    assert analizza("12 marzo 1987") is None
    tempo = collocazione_da_espressione("12 marzo 1987")
    assert tempo is not None
    assert tempo.canonico == "1987-03-12"
    assert tempo.precisione == "giorno"
    con_ora = collocazione_da_espressione("12 marzo 1987, ore 08:15")
    assert con_ora is not None
    assert con_ora.canonico == "1987-03-12T08:15"
    assert collocazione_da_espressione("ieri") is None
    assert orario_da_espressione("ore 11:20") == (11, 20, 0)
    assert orario_da_espressione("alle otto") == (8, 0, 0)
    assert orario_da_espressione("12 marzo 1987") is None
    mezzanotte_ora = collocazione_da_espressione("17 luglio 1960, ore 09:00")
    assert mezzanotte_ora is not None
    assert mezzanotte_ora.canonico == "1960-07-17T09:00"
    assert mezzanotte_ora.precisione == "minuto"
    parlato = collocazione_da_espressione(
        "dodici marzo millenovecentottantasette, alle otto e un quarto"
    )
    assert parlato is not None
    assert parlato.canonico == "1987-03-12T08:15"


def test_intervallo_dal_al():
    coppia = intervallo_da_espressione("Dal 12 marzo 1987 al 15 marzo 1987")
    assert coppia is not None
    assert coppia[0].canonico == "1987-03-12"
    assert coppia[1].canonico == "1987-03-15"
