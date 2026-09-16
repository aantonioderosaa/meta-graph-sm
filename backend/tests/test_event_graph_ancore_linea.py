"""MT4 — timeline forest, implicit intervals, open anchors, SUCCESSIONE_ANCORA."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from app.models.event_graph import (
    NATURA_ANCORE,
    TIPI_ANCORA,
    AncoraTemporaleProposta,
    LivelloAncoreResult,
    SegnaleAncoraEvento,
)
from app.pipeline.event_graph import ancore_linea as linea_mod
from app.pipeline.event_graph.ancore_linea import (
    EVENTO,
    STAGE,
    chiave_ordine_ancora,
    costruisci_linea,
    esegui_ancore_linea,
    violazioni_foresta,
)
from app.pipeline.event_graph.infra import bus as event_graph_bus
from app.pipeline.event_graph.tempo_iso import chiave_ordine

_ETICHETTE_VIETATE = set(NATURA_ANCORE) | set(TIPI_ANCORA)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
MODULE_PATH = PACKAGE_DIR / "ancore_linea.py"


def _ancora(**kwargs) -> AncoraTemporaleProposta:
    kwargs.setdefault("natura", "esplicita")
    kwargs.setdefault("tipo", "data")
    return AncoraTemporaleProposta(**kwargs)


def _per_etichetta(
    ancore: list[AncoraTemporaleProposta],
) -> dict[str, AncoraTemporaleProposta]:
    return {a.etichetta: a for a in ancore}


def _linea(ancore, *, documento: str = "doc-1", segnali=None):
    return costruisci_linea(ancore, documento=documento, segnali=segnali)


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
    if module.startswith("app.pipeline.") and not module.startswith(
        "app.pipeline.event_graph"
    ):
        return True
    return False


def test_1843_contiene_24_dic():
    linea = _linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
        ]
    )
    per = _per_etichetta(linea.ancore)
    assert per["24 dic"].padre == "1843"
    assert per["1843"].padre is None
    assert violazioni_foresta(linea.ancore) == []


def test_1843_contiene_24_dic_per_inferenza_sui_bounds():
    linea = _linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(etichetta="24 dic", inizio="1843-12-24", granularita="giorno"),
        ]
    )
    assert _per_etichetta(linea.ancore)["24 dic"].padre == "1843"
    assert violazioni_foresta(linea.ancore) == []


def test_granularita_uguale_padre_scartato():
    linea = _linea(
        [
            _ancora(
                etichetta="contenitore",
                tipo="relativa",
                granularita="mese",
                natura="esplicita",
            ),
            _ancora(
                etichetta="figlio",
                tipo="relativa",
                granularita="mese",
                padre="contenitore",
                natura="esplicita",
            ),
        ]
    )
    assert _per_etichetta(linea.ancore)["figlio"].padre is None


def test_ciclo_rotto_entrambi_conservati():
    proposti = [
        _ancora(etichetta="alfa", tipo="relativa", padre="beta"),
        _ancora(etichetta="beta", tipo="relativa", padre="alfa"),
    ]
    linea = _linea(proposti)
    per = _per_etichetta(linea.ancore)
    assert set(per) == {"alfa", "beta"}
    assert per["alfa"].padre == "beta"
    assert per["beta"].padre is None
    assert violazioni_foresta(linea.ancore) == []
    ripetuta = _linea(list(reversed(proposti)))
    assert [(a.etichetta, a.padre) for a in ripetuta.ancore] == [
        (a.etichetta, a.padre) for a in linea.ancore
    ]


def test_finestre_disgiunte_scartano_l_arco():
    linea = _linea(
        [
            _ancora(etichetta="1850", inizio="1850", granularita="anno"),
            _ancora(
                etichetta="dic 1843",
                inizio="1843-12",
                granularita="mese",
                padre="1850",
            ),
        ]
    )
    assert _per_etichetta(linea.ancore)["dic 1843"].padre is None
    assert violazioni_foresta(linea.ancore) == []


def test_padre_mancante_non_elimina_l_ancora():
    linea = _linea(
        [_ancora(etichetta="24 dic", inizio="1843-12-24", padre="mai visto")]
    )
    assert len(linea.ancore) == 1
    assert linea.ancore[0].padre is None


def test_intervallo_implicito_fra_24_e_25_dic():
    linea = _linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
            _ancora(
                etichetta="25 dic",
                inizio="1843-12-25",
                granularita="giorno",
                padre="1843",
            ),
        ]
    )
    per = _per_etichetta(linea.ancore)
    intervalli = [a for a in linea.ancore if a.natura == "intervallo"]
    assert len(intervalli) == 1
    intervallo = intervalli[0]
    assert intervallo.padre == "1843"
    assert intervallo.stimato is True
    assert "24 dic" in intervallo.etichetta
    assert "25 dic" in intervallo.etichetta
    assert len(intervallo.etichetta) <= 40
    assert per["24 dic"].padre == "1843"
    assert per["25 dic"].padre == "1843"
    figli = [a.etichetta for a in linea.ancore if a.padre == "1843"]
    assert figli.index("24 dic") < figli.index(intervallo.etichetta) < figli.index(
        "25 dic"
    )


def test_intervallo_datato_quando_c_e_un_buco_di_calendario():
    linea = _linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
            _ancora(
                etichetta="26 dic",
                inizio="1843-12-26",
                granularita="giorno",
                padre="1843",
            ),
        ]
    )
    intervallo = next(a for a in linea.ancore if a.natura == "intervallo")
    assert intervallo.inizio == "1843-12-25"
    assert intervallo.stimato is True
    assert intervallo.padre == "1843"


def test_niente_intervallo_senza_bounds():
    linea = _linea(
        [
            _ancora(etichetta="ieri", tipo="relativa"),
            _ancora(etichetta="Natale", tipo="simbolica"),
        ]
    )
    assert [a for a in linea.ancore if a.natura == "intervallo"] == []


def test_aperta_prima_di_24_dic_da_segnale():
    linea = _linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
        ],
        segnali=[
            SegnaleAncoraEvento(
                evento_id="e-1", ancora="24 dic", posizione="prima"
            )
        ],
    )
    aperte = [a for a in linea.ancore if a.natura == "aperta"]
    assert len(aperte) == 1
    assert aperte[0].padre == "1843"
    assert aperte[0].etichetta.startswith("prima")
    assert aperte[0].etichetta not in _ETICHETTE_VIETATE
    assert all(a.etichetta not in _ETICHETTE_VIETATE for a in linea.ancore)
    figli = [a.etichetta for a in linea.ancore if a.padre == "1843"]
    assert figli[0] == aperte[0].etichetta
    assert figli[1] == "24 dic"


def test_aperta_da_relativa_senza_segnali():
    linea = _linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
            _ancora(
                etichetta="prima di 24 dic",
                tipo="relativa",
                espressione="prima di 24 dic",
            ),
        ]
    )
    aperte = [a for a in linea.ancore if a.natura == "aperta"]
    assert aperte
    assert all(a.padre == "1843" for a in aperte)
    figli = [a for a in linea.ancore if a.padre == "1843"]
    assert figli[0].natura == "aperta"


def test_niente_aperte_senza_evidenza():
    linea = _linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
        ]
    )
    assert [a for a in linea.ancore if a.natura == "aperta"] == []


def test_successione_n_meno_uno_senza_rami_ne_cicli():
    linea = _linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
            _ancora(
                etichetta="25 dic",
                inizio="1843-12-25",
                granularita="giorno",
                padre="1843",
            ),
        ]
    )
    intervallo = next(a for a in linea.ancore if a.natura == "intervallo")
    figli = [a.etichetta for a in linea.ancore if a.padre == "1843"]
    assert figli == ["24 dic", intervallo.etichetta, "25 dic"]
    coppie = [(a, b) for a, b in linea.successione if a in figli and b in figli]
    assert coppie == [
        ("24 dic", intervallo.etichetta),
        (intervallo.etichetta, "25 dic"),
    ]
    assert len(coppie) == len(figli) - 1
    uscenti: dict[str, list[str]] = {}
    for origine, destinazione in coppie:
        uscenti.setdefault(origine, []).append(destinazione)
    assert all(len(dest) == 1 for dest in uscenti.values())
    corrente = figli[0]
    visti = {corrente}
    while corrente in uscenti:
        corrente = uscenti[corrente][0]
        assert corrente not in visti
        visti.add(corrente)
    assert corrente == figli[-1]
    radici = [a.etichetta for a in linea.ancore if a.padre is None]
    coppie_radice = [
        (a, b) for a, b in linea.successione if a in radici and b in radici
    ]
    assert len(coppie_radice) == max(0, len(radici) - 1)


def test_chiave_ordine_padre_anno_prima_del_figlio_giorno():
    linea = _linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
            _ancora(
                etichetta="capodanno",
                inizio="1843-01-01",
                granularita="giorno",
                padre="1843",
            ),
        ]
    )
    assert linea.chiave_ordine["1843"] < linea.chiave_ordine["24 dic"]
    assert linea.chiave_ordine["1843"] < linea.chiave_ordine["capodanno"]
    assert linea.chiave_ordine["1843"] == chiave_ordine("1843", "anno")
    assert linea.chiave_ordine["1843"] < chiave_ordine("1843-01-01", "giorno")
    assert chiave_ordine_ancora(linea.ancore[0]) is not None


def test_ordinale_fra_fratelli():
    linea = _linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
            _ancora(
                etichetta="25 dic",
                inizio="1843-12-25",
                granularita="giorno",
                padre="1843",
            ),
        ]
    )
    figli = [a.etichetta for a in linea.ancore if a.padre == "1843"]
    valori = [linea.ordinale[nome] for nome in figli]
    assert valori == list(range(len(figli)))
    assert linea.ordinale["1843"] == 0


def test_stabile_stesso_input_stesse_etichette_e_successione():
    proposti = [
        _ancora(etichetta="1843", inizio="1843", granularita="anno"),
        _ancora(
            etichetta="24 dic",
            inizio="1843-12-24",
            granularita="giorno",
            padre="1843",
            posizione_doc_min=10,
        ),
        _ancora(
            etichetta="25 dic",
            inizio="1843-12-25",
            granularita="giorno",
            padre="1843",
            posizione_doc_min=20,
        ),
    ]
    segnali = [
        SegnaleAncoraEvento(evento_id="e-1", ancora="24 dic", posizione="prima")
    ]
    prima = _linea(proposti, segnali=segnali)
    seconda = _linea(list(reversed(proposti)), segnali=list(segnali))
    assert [a.etichetta for a in prima.ancore] == [a.etichetta for a in seconda.ancore]
    assert prima.successione == seconda.successione
    assert {a.etichetta for a in prima.ancore} == {a.etichetta for a in seconda.ancore}


def test_non_inventa_alberi_dalla_sola_sequenza():
    linea = _linea(
        [
            _ancora(
                etichetta="24 dic", inizio="1843-12-24", granularita="giorno"
            ),
            _ancora(
                etichetta="25 dic", inizio="1843-12-25", granularita="giorno"
            ),
        ]
    )
    per = _per_etichetta(linea.ancore)
    assert per["24 dic"].padre is None
    assert per["25 dic"].padre is None


def test_al_piu_un_padre():
    linea = _linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(etichetta="dic", inizio="1843-12", granularita="mese", padre="1843"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
        ]
    )
    padri = [a.padre for a in linea.ancore]
    assert all(p is None or isinstance(p, str) for p in padri)
    assert violazioni_foresta(linea.ancore) == []
    giorno = _per_etichetta(linea.ancore)["24 dic"]
    assert giorno.padre in {"1843", "dic"}


def test_input_malformato_non_solleva():
    for spazzatura in (None, 0, "non una lista", [None, 3, "x"], {"etichetta": "1843"}):
        linea = costruisci_linea(spazzatura, documento="doc-1")
        assert linea.ancore == []
        assert linea.successione == []
        assert violazioni_foresta(spazzatura) == []


def test_livello_ancore_result_passa_i_segnali():
    livello = LivelloAncoreResult(
        ancore=[
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
        ],
        segnali=[
            SegnaleAncoraEvento(evento_id="e-1", ancora="24 dic", posizione="dopo")
        ],
    )
    linea = costruisci_linea(livello, documento="doc-1")
    assert any(a.natura == "aperta" and a.etichetta.startswith("dopo") for a in linea.ancore)


def test_esegui_pubblica_il_riepilogo_sul_bus():
    async def _run() -> None:
        job_id = "job-linea"
        queue = await event_graph_bus.subscribe(job_id)
        try:
            out = await esegui_ancore_linea(
                [
                    _ancora(etichetta="1843", inizio="1843", granularita="anno"),
                    _ancora(
                        etichetta="24 dic",
                        inizio="1843-12-24",
                        granularita="giorno",
                        padre="1843",
                    ),
                    _ancora(
                        etichetta="25 dic",
                        inizio="1843-12-25",
                        granularita="giorno",
                        padre="1843",
                    ),
                ],
                documento="doc-1",
                job_id=job_id,
            )
            eventi = []
            while True:
                try:
                    eventi.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            riepiloghi = [item for item in eventi if item["event"] == EVENTO]
            assert riepiloghi
            assert riepiloghi[0]["stage"] == STAGE
            payload = riepiloghi[0]["payload"]
            assert payload["n_in"] == 3
            assert payload["n_intervalli"] == 1
            assert payload["n_aperte"] == 0
            assert payload["n_successione"] == out.n_successione
        finally:
            await event_graph_bus.unsubscribe(job_id, queue)
            event_graph_bus.reset_event_bus()

    asyncio.run(_run())


def test_nessun_import_llm_e_isolamento_d6():
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE_PATH))
    modules = _import_modules(tree)
    violations = [
        f"{MODULE_PATH}: {module}" for module in modules if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "app.core" not in source
    assert "call_structured" not in source
    assert "infra.llm" not in source
    assert "foresta_temporale" not in source
    assert "ClusterTemporale" not in source
    assert "livello_temporale" not in source
    compacted = source.replace("\n", " ")
    assert "except Exception:" not in compacted
    assert "except Exception as" not in compacted
    llm_mods = [
        module
        for module in modules
        if "llm" in module.casefold() or module.endswith(".openai")
    ]
    assert llm_mods == []
    assert not any("neo4j" in module.casefold() for module in modules)
    assert 'or "ancora"' not in source


def test_riserva_etichetta_vuota_non_diventa_natura_o_tipo():
    nome = linea_mod._riserva_etichetta("", set())
    assert nome not in _ETICHETTE_VIETATE


def test_etichette_sintetiche_non_coincidono_con_natura_o_tipo():
    linea = _linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
            _ancora(
                etichetta="26 dic",
                inizio="1843-12-26",
                granularita="giorno",
                padre="1843",
            ),
        ],
        segnali=[
            SegnaleAncoraEvento(
                evento_id="e-1", ancora="24 dic", posizione="prima"
            )
        ],
    )
    assert linea_mod._etichetta_fra("24 dic", "26 dic").startswith("fra")
    assert linea_mod._etichetta_lato("prima", "24 dic").startswith("prima di")
    assert linea_mod._etichetta_lato("dopo", "24 dic").startswith("dopo")
    assert all(a.etichetta not in _ETICHETTE_VIETATE for a in linea.ancore)
    assert any(a.natura == "intervallo" for a in linea.ancore)
    assert any(a.natura == "aperta" for a in linea.ancore)

