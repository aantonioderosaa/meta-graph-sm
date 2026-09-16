"""MT4 / Parte B — riconciliazione dei cluster e foresta CONTIENE.

Casi degeneri, come chiede `PIANO-LIVELLO-TEMPORALE-V2.md`: etichette che
collidono, collocazioni che si riformulano, padri impossibili, cicli. Nessuna
chiamata LLM: le funzioni di riconciliazione sono pure, e i due test che
passano dalla pipeline usano lo stub di `call_structured`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import (
    ClusterTemporaleProposto,
    EventoRisolto,
    LivelloTemporaleResult,
    SegnaleTemporaleEvento,
)
from app.pipeline.event_graph import foresta_temporale as ft
from app.pipeline.event_graph.livello_temporale import (
    CONFIDENZA_COLLOCAZIONE_INCERTA,
    _merge_results,
    _sanitize,
    estrai_livello_temporale,
    eventi_senza_collocazione,
)

FORESTA_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "pipeline"
    / "event_graph"
    / "foresta_temporale.py"
)


def _cluster(
    etichetta: str,
    *,
    tipo: str = "data_esplicita",
    inizio: str | None = None,
    fine: str | None = None,
    granularita: str | None = None,
    eventi: list[str] | None = None,
    padre: str | None = None,
    descrizione: str | None = None,
    confidenza: float = 1.0,
    stimato: bool = False,
) -> ClusterTemporaleProposto:
    return ClusterTemporaleProposto(
        etichetta=etichetta,
        tipo=tipo,
        inizio=inizio,
        fine=fine,
        granularita=granularita,
        eventi=list(eventi or []),
        padre=padre,
        descrizione=descrizione,
        confidenza=confidenza,
        stimato=stimato,
    )


def _evento(i: int) -> EventoRisolto:
    return EventoRisolto(id=f"e-{i}", lemma="x", span=f"evento {i}", posizione_doc=i)


def _per_etichetta(
    clusters: list[ClusterTemporaleProposto],
) -> dict[str, ClusterTemporaleProposto]:
    return {c.etichetta: c for c in clusters}


def _install_stub(monkeypatch, handler):
    async def stub(
        system_prompt, user_prompt, response_model, temperature=0, job_id=None
    ):
        return await handler(
            system_prompt, user_prompt, response_model, temperature, job_id
        )

    monkeypatch.setattr(
        "app.pipeline.event_graph.livello_temporale.call_structured", stub
    )


# --- fusione per collocazione ----------------------------------------------


def test_etichette_diverse_stessa_collocazione_si_fondono():
    fusi = ft.fondi_per_collocazione(
        [
            _cluster(
                "Prova del Vento: soffio violento e resistenza dell'uomo",
                inizio="1843-12-24T18",
                granularita="ora",
                eventi=["e-0"],
            ),
            _cluster(
                "24 dic, sera",
                inizio="1843-12-24T18",
                granularita="ora",
                eventi=["e-1"],
            ),
        ]
    ).cluster

    assert len(fusi) == 1
    # vince l'etichetta che rispetta il limite del piano
    assert fusi[0].etichetta == "24 dic, sera"
    assert fusi[0].eventi == ["e-0", "e-1"]
    # la prosa scartata non si perde: diventa la descrizione
    assert fusi[0].descrizione == (
        "Prova del Vento: soffio violento e resistenza dell'uomo"
    )


def test_fra_due_etichette_brevi_vince_la_piu_informativa():
    fusi = ft.fondi_per_collocazione(
        [
            _cluster("sera", inizio="1843-12-24T18", granularita="ora"),
            _cluster("24 dic 1843, sera", inizio="1843-12-24T18", granularita="ora"),
        ]
    ).cluster

    assert len(fusi) == 1
    assert fusi[0].etichetta == "24 dic 1843, sera"
    # una scartata che sta nel limite non è prosa: non finisce in descrizione
    assert fusi[0].descrizione is None


def test_stessa_etichetta_inizio_diverso_non_si_fonde():
    """La collisione che MT3 aveva lasciato aperta: due sere diverse."""
    proposti = [
        _cluster("sera", inizio="1843-12-24T18", granularita="ora", eventi=["e-0"]),
        _cluster("sera", inizio="1844-12-24T18", granularita="ora", eventi=["e-1"]),
    ]

    fusi = ft.fondi_per_collocazione(proposti).cluster
    assert len(fusi) == 2
    assert [c.inizio for c in fusi] == ["1843-12-24T18", "1844-12-24T18"]

    # e in uscita dalla foresta le etichette sono distinte, perché è l'etichetta
    # che MT5 usa per costruire l'id del nodo
    foresta = ft.costruisci_foresta(proposti)
    etichette = [c.etichetta for c in foresta]
    assert etichette == ["sera", "sera (1844-12-24T18)"]
    assert ft.violazioni_foresta(foresta) == []


def test_riformulazioni_della_stessa_stringa_si_fondono():
    fusi = ft.fondi_per_collocazione(
        [
            _cluster("cena", inizio="1843-12-24 18:30", eventi=["e-0"]),
            _cluster("cena", inizio=" 1843-12-24T18:30Z ", eventi=["e-1"]),
            _cluster("cena", inizio="1843-12-24T18:30", eventi=["e-2"]),
        ]
    ).cluster

    assert len(fusi) == 1
    assert fusi[0].eventi == ["e-0", "e-1", "e-2"]


def test_granularita_dichiarata_diversa_tiene_separati_i_cluster():
    """`(inizio, granularita)` è la chiave: un giorno non è il mese che lo contiene."""
    fusi = ft.fondi_per_collocazione(
        [
            _cluster("il giorno", inizio="1843-12-24", granularita="giorno"),
            _cluster("il mese", inizio="1843-12-24", granularita="mese"),
        ]
    ).cluster

    assert len(fusi) == 2


def test_cluster_senza_inizio_si_fonde_per_etichetta():
    fusi = ft.fondi_per_collocazione(
        [
            _cluster("sette anni prima", tipo="relativo", eventi=["e-0"]),
            _cluster("Sette  Anni Prima", tipo="relativo", eventi=["e-1"]),
            _cluster("molti anni prima", tipo="relativo", eventi=["e-2"]),
        ]
    ).cluster

    assert len(fusi) == 2
    assert fusi[0].eventi == ["e-0", "e-1"]
    assert fusi[1].eventi == ["e-2"]


def test_cluster_senza_inizio_viene_saldato_al_datato_omonimo():
    """Il comportamento utile della fusione per etichetta di MT3, conservato."""
    fusi = ft.fondi_per_collocazione(
        [
            _cluster("24 dic, sera", granularita="ora", eventi=["e-0"], padre="1843"),
            _cluster("24 dic, sera", inizio="1843-12-24T18", eventi=["e-1"]),
        ]
    ).cluster

    assert len(fusi) == 1
    assert fusi[0].eventi == ["e-0", "e-1"]
    assert fusi[0].inizio == "1843-12-24T18"
    assert fusi[0].padre == "1843"


def test_saldatura_ambigua_lascia_il_cluster_senza_inizio_a_se():
    """Due momenti con la stessa etichetta: a quale dei due apparterrebbe?"""
    fusi = ft.fondi_per_collocazione(
        [
            _cluster("sera", inizio="1843-12-24T18", eventi=["e-0"]),
            _cluster("sera", inizio="1844-12-24T18", eventi=["e-1"]),
            _cluster("sera", tipo="relativo", eventi=["e-2"]),
        ]
    ).cluster

    assert len(fusi) == 3
    assert [c.eventi for c in fusi] == [["e-0"], ["e-1"], ["e-2"]]


def test_fondi_conserva_la_semantica_dei_campi_di_mt3():
    fusi = ft.fondi_per_collocazione(
        [
            _cluster(
                "sera",
                inizio="1843-12-24T18",
                granularita="ora",
                eventi=["e-0"],
                stimato=True,
                confidenza=0.9,
                padre="1843",
            ),
            _cluster(
                "sera",
                inizio="1843-12-24T18",
                fine="1843-12-24T20",
                descrizione="dopo la chiusura dell'ufficio",
                eventi=["e-1"],
                stimato=False,
                confidenza=0.7,
            ),
        ]
    ).cluster

    fuso = fusi[0]
    assert fuso.eventi == ["e-0", "e-1"]
    assert fuso.granularita == "ora"
    assert fuso.padre == "1843"
    assert fuso.fine == "1843-12-24T20"
    assert fuso.descrizione == "dopo la chiusura dell'ufficio"
    assert fuso.stimato is False
    assert fuso.confidenza == 0.7


def test_vocabolario_mappa_gli_alias_sul_cluster_fuso():
    riconciliazione = ft.fondi_per_collocazione(
        [
            _cluster("la sera della vigilia di Natale del 1843", inizio="1843-12-24T18"),
            _cluster("24 dic, sera", inizio="1843-12-24T18"),
        ]
    )

    chiave = ft.chiave_collocazione(riconciliazione.cluster[0])
    assert riconciliazione.etichette["24 dic, sera"] == chiave
    # l'etichetta scartata resta risolvibile: i `padre` scritti dall'LLM la usano
    assert (
        riconciliazione.etichette["la sera della vigilia di natale del 1843"] == chiave
    )


def test_vocabolario_segna_ambigua_una_etichetta_di_due_cluster():
    riconciliazione = ft.fondi_per_collocazione(
        [
            _cluster("sera", inizio="1843-12-24T18"),
            _cluster("sera", inizio="1844-12-24T18"),
        ]
    )
    assert riconciliazione.etichette["sera"] is None


def test_fondi_per_collocazione_e_idempotente():
    proposti = [
        _cluster("1843", inizio="1843", eventi=[]),
        _cluster("24 dic", inizio="1843-12-24", eventi=["e-0"], padre="1843"),
        _cluster("24 dicembre", inizio="1843-12-24", eventi=["e-1"], padre="1843"),
    ]
    una = ft.fondi_per_collocazione(proposti).cluster
    due = ft.fondi_per_collocazione(una).cluster
    assert [c.model_dump() for c in una] == [c.model_dump() for c in due]


# --- foresta ----------------------------------------------------------------


def test_annidamento_a_due_livelli_conservato():
    foresta = ft.costruisci_foresta(
        [
            _cluster("24 dic, sera", inizio="1843-12-24T18", eventi=["e-0"], padre="24 dic"),
            _cluster("24 dic", inizio="1843-12-24", padre="1843"),
            _cluster("1843", inizio="1843"),
        ]
    )

    # l'ordine di uscita è canonico: il contenitore prima del contenuto
    assert [c.etichetta for c in foresta] == ["1843", "24 dic", "24 dic, sera"]
    per_etichetta = _per_etichetta(foresta)
    assert per_etichetta["1843"].padre is None
    assert per_etichetta["24 dic"].padre == "1843"
    assert per_etichetta["24 dic, sera"].padre == "24 dic"
    assert ft.archi_contiene(foresta) == [
        ("1843", "24 dic"),
        ("24 dic", "24 dic, sera"),
    ]
    assert ft.violazioni_foresta(foresta) == []


def test_padre_con_granularita_uguale_scartato():
    foresta = ft.costruisci_foresta(
        [
            _cluster("contenitore", tipo="relativo", granularita="mese"),
            _cluster(
                "figlio", tipo="relativo", granularita="mese", padre="contenitore"
            ),
        ]
    )
    assert _per_etichetta(foresta)["figlio"].padre is None


def test_padre_con_granularita_piu_fine_scartato():
    foresta = ft.costruisci_foresta(
        [
            _cluster("contenitore", tipo="relativo", granularita="giorno"),
            _cluster("figlio", tipo="relativo", granularita="mese", padre="contenitore"),
        ]
    )
    assert _per_etichetta(foresta)["figlio"].padre is None


def test_padre_piu_grosso_senza_date_accettato():
    """Senza `inizio` la granularità è l'unico vincolo, e regge l'annidamento."""
    foresta = ft.costruisci_foresta(
        [
            _cluster("l'anno del racconto", tipo="relativo", granularita="anno"),
            _cluster(
                "sette anni prima",
                tipo="relativo",
                granularita="giorno",
                padre="l'anno del racconto",
            ),
        ]
    )
    assert _per_etichetta(foresta)["sette anni prima"].padre == "l'anno del racconto"
    assert ft.violazioni_foresta(foresta) == []


def test_granularita_ignota_non_blocca_l_annidamento():
    foresta = ft.costruisci_foresta(
        [
            _cluster("un tempo remoto", tipo="simbolico"),
            _cluster(
                "sette anni prima",
                tipo="relativo",
                granularita="giorno",
                padre="un tempo remoto",
            ),
        ]
    )
    assert _per_etichetta(foresta)["sette anni prima"].padre == "un tempo remoto"


def test_finestre_disgiunte_scartano_l_arco():
    """Padre `1850`, figlio `1843-12`: granularità giusta, annidamento falso."""
    foresta = ft.costruisci_foresta(
        [
            _cluster("1850", inizio="1850"),
            _cluster("dic 1843", inizio="1843-12", padre="1850"),
        ]
    )
    assert _per_etichetta(foresta)["dic 1843"].padre is None
    assert ft.violazioni_foresta(foresta) == []


def test_intervallo_del_padre_letto_anche_dalla_fine():
    """Un padre `intervallo` è largo fino a `fine`, non solo la sua granularità."""
    foresta = ft.costruisci_foresta(
        [
            _cluster(
                "l'inverno",
                tipo="intervallo",
                inizio="1843-12",
                fine="1844-02",
                granularita="mese",
            ),
            _cluster("2 gen 1844", inizio="1844-01-02", padre="l'inverno"),
        ]
    )
    assert _per_etichetta(foresta)["2 gen 1844"].padre == "l'inverno"


def test_granularita_effettiva_vince_su_quella_dichiarata():
    """Un `inizio="1843"` con `granularita="secondo"` occupa un anno, non un secondo."""
    dichiarazione_impossibile = _cluster(
        "l'anno", inizio="1843", granularita="secondo"
    )
    assert ft.granularita_effettiva(dichiarazione_impossibile) == "anno"

    foresta = ft.costruisci_foresta(
        [
            dichiarazione_impossibile,
            _cluster("dic 1843", inizio="1843-12", padre="l'anno"),
        ]
    )
    # letto sulla dichiarazione l'arco sarebbe stato scartato (secondo < mese)
    assert _per_etichetta(foresta)["dic 1843"].padre == "l'anno"


def test_padre_inesistente_azzerato():
    foresta = ft.costruisci_foresta(
        [_cluster("24 dic", inizio="1843-12-24", padre="un cluster mai proposto")]
    )
    assert foresta[0].padre is None
    assert ft.violazioni_foresta(foresta) == []


def test_padre_ambiguo_azzerato():
    """Due cluster con la stessa etichetta: il riferimento non dice quale."""
    foresta = ft.costruisci_foresta(
        [
            _cluster("sera", inizio="1843-12-24T18"),
            _cluster("sera", inizio="1844-12-24T18"),
            _cluster("un minuto", inizio="1843-12-24T18:30", padre="sera"),
        ]
    )
    assert _per_etichetta(foresta)["un minuto"].padre is None


def test_padre_di_se_stesso_azzerato():
    foresta = ft.costruisci_foresta(
        [_cluster("24 dic", inizio="1843-12-24", padre="24 DIC")]
    )
    assert foresta[0].padre is None


def test_ciclo_di_due_rotto_deterministicamente():
    proposti = [
        _cluster("alfa", tipo="relativo", padre="beta"),
        _cluster("beta", tipo="relativo", padre="alfa"),
    ]
    foresta = ft.costruisci_foresta(proposti)
    per_etichetta = _per_etichetta(foresta)

    # in ordine canonico "alfa" viene prima e tiene il suo arco; l'arco che
    # chiuderebbe l'anello è quello dell'ultimo figlio, e cade
    assert per_etichetta["alfa"].padre == "beta"
    assert per_etichetta["beta"].padre is None
    assert ft.violazioni_foresta(foresta) == []
    # e l'ordine di ingresso non cambia il risultato
    assert [
        (c.etichetta, c.padre) for c in ft.costruisci_foresta(list(reversed(proposti)))
    ] == [(c.etichetta, c.padre) for c in foresta]


def test_ciclo_di_tre_rotto_deterministicamente():
    proposti = [
        _cluster("alfa", tipo="relativo", padre="beta"),
        _cluster("beta", tipo="relativo", padre="gamma"),
        _cluster("gamma", tipo="relativo", padre="alfa"),
    ]
    foresta = ft.costruisci_foresta(proposti)
    per_etichetta = _per_etichetta(foresta)

    assert per_etichetta["alfa"].padre == "beta"
    assert per_etichetta["beta"].padre == "gamma"
    assert per_etichetta["gamma"].padre is None
    assert ft.violazioni_foresta(foresta) == []
    assert len(ft.archi_contiene(foresta)) == 2


def test_due_cluster_fusi_con_padri_diversi_tengono_il_piu_prossimo():
    proposti = [
        _cluster("1843", inizio="1843"),
        _cluster("dic 1843", inizio="1843-12", padre="1843"),
        _cluster("24 dic, sera", inizio="1843-12-24T18", padre="1843"),
        _cluster("la sera", inizio="1843-12-24T18", padre="dic 1843"),
    ]
    foresta = ft.costruisci_foresta(proposti)

    assert len(foresta) == 3
    # fra due padri dichiarati per lo stesso cluster vince l'antenato più
    # prossimo: `1843` si raggiunge risalendo da `dic 1843`
    fuso = next(c for c in foresta if c.inizio == "1843-12-24T18")
    assert fuso.padre == "dic 1843"
    assert ft.violazioni_foresta(foresta) == []
    # e la scelta non dipende dall'ordine delle finestre
    assert [
        (c.etichetta, c.padre) for c in ft.costruisci_foresta(list(reversed(proposti)))
    ] == [(c.etichetta, c.padre) for c in foresta]


def test_padre_non_verificabile_perde_contro_un_padre_verificabile():
    proposti = [
        _cluster("1843", inizio="1843"),
        _cluster("chissà quando", tipo="simbolico"),
        _cluster("24 dic", inizio="1843-12-24", padre="1843"),
        _cluster("24 dicembre", inizio="1843-12-24", padre="chissà quando"),
    ]
    foresta = ft.costruisci_foresta(proposti)
    fuso = next(c for c in foresta if c.inizio == "1843-12-24")
    assert fuso.padre == "1843"


def test_foresta_deterministica_all_ordine_di_ingresso():
    proposti = [
        _cluster("1843", inizio="1843"),
        _cluster("dic 1843", inizio="1843-12", padre="1843"),
        _cluster("24 dic", inizio="1843-12-24", padre="dic 1843", eventi=["e-0"]),
        _cluster("25 dic", inizio="1843-12-25", padre="dic 1843", eventi=["e-1"]),
        _cluster("1850", inizio="1850", padre="dic 1843"),
        _cluster("sette anni prima", tipo="relativo", padre="1843"),
    ]
    attesa = ft.costruisci_foresta(proposti)
    for ruotato in (
        list(reversed(proposti)),
        proposti[3:] + proposti[:3],
        proposti[1:] + proposti[:1],
    ):
        assert [(c.etichetta, c.padre) for c in ft.costruisci_foresta(ruotato)] == [
            (c.etichetta, c.padre) for c in attesa
        ]
    assert ft.violazioni_foresta(attesa) == []
    assert ft.archi_contiene(attesa)


def test_foresta_non_perde_cluster_quando_scarta_un_arco():
    foresta = ft.costruisci_foresta(
        [
            _cluster("1850", inizio="1850"),
            _cluster("dic 1843", inizio="1843-12", padre="1850", eventi=["e-0"]),
        ]
    )
    assert {c.etichetta for c in foresta} == {"1850", "dic 1843"}
    assert _per_etichetta(foresta)["dic 1843"].eventi == ["e-0"]


def test_violazioni_foresta_riconosce_una_gerarchia_rotta():
    rotta = [
        _cluster("giorno", inizio="1843-12-24", granularita="giorno"),
        _cluster("anno", inizio="1843", granularita="anno", padre="giorno"),
        _cluster("orfano", tipo="relativo", padre="mai visto"),
        _cluster("solitario", tipo="relativo", padre="solitario"),
    ]
    violazioni = ft.violazioni_foresta(rotta)
    assert "granularita non decrescente: giorno -> anno" in violazioni
    assert "padre irrisolvibile: orfano -> mai visto" in violazioni
    assert "padre di se stesso: solitario" in violazioni


def test_violazioni_foresta_riconosce_un_ciclo_e_un_doppione():
    rotta = [
        _cluster("alfa", tipo="relativo", padre="beta"),
        _cluster("beta", tipo="relativo", padre="alfa"),
        _cluster("beta", tipo="relativo"),
    ]
    violazioni = ft.violazioni_foresta(rotta)
    assert any(v.startswith("ciclo su:") for v in violazioni)
    assert "etichetta duplicata: beta" in violazioni


# --- potatura dei contenitori sterili ---------------------------------------


def test_contenitore_resta_se_regge_un_figlio_vivo():
    potata = ft.scarta_contenitori_sterili(
        ft.costruisci_foresta(
            [
                _cluster("1843", inizio="1843"),
                _cluster("24 dic", inizio="1843-12-24", padre="1843", eventi=["e-0"]),
            ]
        )
    )
    assert [c.etichetta for c in potata] == ["1843", "24 dic"]


def test_contenitore_sparisce_se_la_foresta_gli_toglie_il_figlio():
    """Il gate lo aveva tenuto in vita come padre; l'arco però non è valido."""
    proposti = [
        _cluster("24 dic", inizio="1843-12-24", granularita="giorno"),
        _cluster("1843", inizio="1843", granularita="anno", padre="24 dic", eventi=["e-0"]),
    ]
    potata = ft.scarta_contenitori_sterili(ft.costruisci_foresta(proposti))
    assert [c.etichetta for c in potata] == ["1843"]
    assert potata[0].padre is None


def test_potatura_a_punto_fisso_su_una_catena_di_contenitori():
    potata = ft.scarta_contenitori_sterili(
        [
            _cluster("nonno", tipo="relativo", granularita="secolo"),
            _cluster("padre", tipo="relativo", granularita="anno", padre="nonno"),
            _cluster("figlio", tipo="relativo", granularita="giorno", padre="padre"),
        ]
    )
    assert potata == []


def test_potatura_non_risuscita_nulla():
    vivi = [
        _cluster("nonno", tipo="relativo", granularita="secolo"),
        _cluster("padre", tipo="relativo", granularita="anno", padre="nonno"),
        _cluster(
            "figlio",
            tipo="relativo",
            granularita="giorno",
            padre="padre",
            eventi=["e-0"],
        ),
    ]
    assert [c.etichetta for c in ft.scarta_contenitori_sterili(vivi)] == [
        "nonno",
        "padre",
        "figlio",
    ]


# --- chiave d'ordine --------------------------------------------------------


def test_chiave_ordine_cluster_monotona_e_padre_prima_del_figlio():
    anno = _cluster("1843", inizio="1843")
    mese = _cluster("dic 1843", inizio="1843-12")
    dopo = _cluster("1844", inizio="1844")

    chiave_anno = ft.chiave_ordine_cluster(anno)
    chiave_mese = ft.chiave_ordine_cluster(mese)
    assert chiave_anno is not None and chiave_mese is not None
    # a pari istante il contenitore precede il contenuto; poi vince il tempo
    assert chiave_anno < chiave_mese < ft.chiave_ordine_cluster(dopo)
    assert ft.chiave_ordine_cluster(_cluster("mai", tipo="simbolico")) is None


def test_chiavi_ordine_pronte_per_mt5():
    chiavi = ft.chiavi_ordine(
        [_cluster("1843", inizio="1843"), _cluster("mai", tipo="simbolico")]
    )
    assert chiavi["1843"] == ft.chiave_ordine_cluster(_cluster("1843", inizio="1843"))
    assert chiavi["mai"] is None


# --- tolleranza -------------------------------------------------------------


@pytest.mark.parametrize(
    "spazzatura",
    [None, 0, "non una lista", [None, 3, "x"], [{"etichetta": "1843"}]],
)
def test_nessuna_funzione_solleva_su_input_malformato(spazzatura):
    assert ft.fondi_per_collocazione(spazzatura).cluster == []
    assert ft.costruisci_foresta(spazzatura) == []
    assert ft.scarta_contenitori_sterili(spazzatura) == []
    assert ft.archi_contiene(spazzatura) == []
    assert ft.chiavi_ordine(spazzatura) == {}
    assert ft.violazioni_foresta(spazzatura) == []
    assert ft.mappa_etichette(spazzatura) == {}


def test_cluster_senza_etichetta_ne_inizio_non_e_identificabile():
    senza_tutto = _cluster("   ", tipo="simbolico")
    assert ft.chiave_collocazione(senza_tutto) is None
    assert ft.costruisci_foresta([senza_tutto]) == []


def test_inizio_non_parsabile_ricade_sull_etichetta():
    cluster = _cluster("verso il 1840", tipo="relativo", inizio="circa 1840")
    assert ft.chiave_collocazione(cluster) == (ft.CHIAVE_ETICHETTA, "verso il 1840", "")


# --- integrazione con il livello temporale ----------------------------------


def test_merge_results_fonde_finestre_con_etichette_diverse():
    prima = LivelloTemporaleResult(
        cluster=[
            _cluster(
                "la sera in cui Marley tornò a bussare alla porta di Scrooge",
                inizio="1843-12-24 18:30",
                eventi=["e-0"],
            )
        ]
    )
    seconda = LivelloTemporaleResult(
        cluster=[_cluster("24 dic, sera", inizio="1843-12-24T18:30Z", eventi=["e-1"])]
    )

    merged = _merge_results([prima, seconda])

    assert len(merged.cluster) == 1
    assert merged.cluster[0].etichetta == "24 dic, sera"
    assert merged.cluster[0].eventi == ["e-0", "e-1"]


def test_sanitize_restituisce_una_foresta_valida():
    eventi = [_evento(0), _evento(1)]
    result = LivelloTemporaleResult(
        segnali=[SegnaleTemporaleEvento(evento_id="e-0", tempo_assoluto="1843-12-24")],
        cluster=[
            _cluster("24 dic, sera", inizio="1843-12-24T18", eventi=["e-0"], padre="1843"),
            _cluster("1843", inizio="1843", padre="24 dic, sera"),
            _cluster("25 dic", inizio="1843-12-25", eventi=["e-1"], padre="1843"),
        ],
    )

    sanitized = _sanitize(result, eventi)

    assert ft.violazioni_foresta(sanitized.cluster) == []
    per_etichetta = _per_etichetta(sanitized.cluster)
    # il ciclo dichiarato (`1843` dentro `24 dic, sera`) è rotto dalla parte del
    # padre di granularità più fine, che non poteva contenere l'anno
    assert per_etichetta["1843"].padre is None
    assert per_etichetta["24 dic, sera"].padre == "1843"
    assert per_etichetta["25 dic"].padre == "1843"
    assert ft.archi_contiene(sanitized.cluster) == [
        ("1843", "24 dic, sera"),
        ("1843", "25 dic"),
    ]


def test_sanitize_tiene_il_gate_a_06_e_non_risuscita_i_contenitori():
    eventi = [_evento(0), _evento(1)]
    result = LivelloTemporaleResult(
        cluster=[
            _cluster("1843", inizio="1843", confidenza=0.2),
            _cluster(
                "24 dic",
                inizio="1843-12-24",
                eventi=["e-0"],
                padre="1843",
                confidenza=0.9,
            ),
            _cluster("25 dic", inizio="1843-12-25", eventi=["e-1"], confidenza=0.59),
        ],
    )

    sanitized = _sanitize(result, eventi)

    per_etichetta = _per_etichetta(sanitized.cluster)
    # il contenitore sotto soglia sopravvive svuotato perché regge un figlio vivo
    assert per_etichetta["1843"].eventi == []
    assert per_etichetta["24 dic"].eventi == ["e-0"]
    assert per_etichetta["24 dic"].padre == "1843"
    # il cluster sotto soglia perde i membri e, senza figli, sparisce
    assert "25 dic" not in per_etichetta
    # e-1 resta scoperto (né segnale diretto né appartenenza): §B11 gli crea un
    # sottocluster a bassa confidenza agganciato al cluster collocato più
    # vicino ("24 dic"), invece di lasciarlo una foglia senza traccia
    incerto = per_etichetta["collocazione incerta — 24 dic"]
    assert incerto.eventi == ["e-1"]
    assert incerto.padre == "24 dic"
    assert incerto.confidenza == CONFIDENZA_COLLOCAZIONE_INCERTA
    # "24 dic" ha un inizio reale: risalendo il padre del sottocluster, e-1 è
    # ora collocato (per quanto a bassa confidenza) — non più una foglia persa
    assert eventi_senza_collocazione(eventi, sanitized) == []


def test_eventi_senza_collocazione_risale_la_foresta():
    eventi = [_evento(0)]
    result = LivelloTemporaleResult(
        cluster=[
            _cluster("1843", inizio="1843"),
            _cluster(
                "sette anni prima",
                tipo="relativo",
                granularita="giorno",
                eventi=["e-0"],
                padre="1843",
            ),
        ]
    )
    sanitized = _sanitize(result, eventi)
    assert _per_etichetta(sanitized.cluster)["sette anni prima"].inizio is None
    # collocato dall'antenato, che è come il piano ricava i cluster più grossi
    assert eventi_senza_collocazione(eventi, sanitized) == []


@pytest.mark.asyncio
async def test_estrai_fonde_le_finestre_per_collocazione(monkeypatch):
    eventi = [_evento(i) for i in range(61)]
    chiamate: list[int] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        chiamate.append(1)
        prima = len(chiamate) == 1
        return LivelloTemporaleResult(
            segnali=[
                SegnaleTemporaleEvento(
                    evento_id="e-0" if prima else "e-60", tempo_assoluto="1843-12-24"
                )
            ],
            cluster=[
                _cluster("1843", tipo="intervallo", inizio="1843"),
                _cluster(
                    "vigilia" if prima else "24 dic, la vigilia",
                    inizio="1843-12-24",
                    granularita=None if prima else "giorno",
                    eventi=["e-0"] if prima else ["e-60"],
                    padre="1843",
                ),
            ],
        )

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_temporale(eventi)

    assert len(chiamate) == 2
    assert result is not None
    # le due finestre hanno etichettato lo stesso giorno in due modi diversi, e
    # una sola delle due ha dichiarato la granularità che l'altra implica: è la
    # stessa collocazione, quindi il cluster è uno
    figlio = next(c for c in result.cluster if c.etichetta == "24 dic, la vigilia")
    assert figlio.eventi == ["e-0", "e-60"]
    assert figlio.padre == "1843"
    # e-1..e-59: lo stub non emette nessun segnale né appartenenza per loro,
    # quindi §B11 li raccoglie in un sottocluster a bassa confidenza agganciato
    # all'unico cluster collocato — non è più un caso isolato da un solo test,
    # è lo stesso meccanismo di test_sanitize_tiene_il_gate_a_06_...
    incerto = next(
        c for c in result.cluster if c.etichetta.startswith("collocazione incerta")
    )
    assert incerto.padre == "24 dic, la vigilia"
    assert incerto.confidenza == CONFIDENZA_COLLOCAZIONE_INCERTA
    assert incerto.eventi == [f"e-{i}" for i in range(1, 60)]
    assert len(result.cluster) == 3
    assert ft.violazioni_foresta(result.cluster) == []
    assert set(ft.archi_contiene(result.cluster)) == {
        ("1843", "24 dic, la vigilia"),
        ("24 dic, la vigilia", incerto.etichetta),
    }


# --- isolamento D6 ----------------------------------------------------------


def _import_modules(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            base = node.module or ""
            modules.append(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                modules.append(f"{base}.{alias.name}" if base else alias.name)
    return modules


def _is_forbidden_import(module: str) -> bool:
    if module == "app.core" or module.startswith("app.core."):
        return True
    if module == "app.pipeline":
        return True
    return module.startswith("app.pipeline.") and not module.startswith(
        "app.pipeline.event_graph"
    )


def test_foresta_temporale_isolation_ast():
    source = FORESTA_PATH.read_text(encoding="utf-8")
    modules = _import_modules(ast.parse(source, filename=str(FORESTA_PATH)))
    assert [module for module in modules if _is_forbidden_import(module)] == []
    assert all("app.core" not in module for module in modules)
    # la riconciliazione è pura: qui non si parla né col modello né col database
    assert all("infra" not in module for module in modules)
    assert all(module not in {"openai", "neo4j"} for module in modules)
