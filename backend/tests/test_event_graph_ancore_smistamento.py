"""MT5 — assign events to leaf ancore; sterile prune; unplaced stay free."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from app.models.event_graph import (
    NATURA_ANCORE,
    TIPI_ANCORA,
    AncoraTemporaleProposta,
    ArgomentoRisolto,
    EventoRisolto,
    LivelloAncoreResult,
    MenzioneRisolta,
    SegnaleAncoraEvento,
)
from app.pipeline.event_graph import ancore_smistamento as smistamento_mod
from app.pipeline.event_graph.ancore_linea import LineaAncore, costruisci_linea
from app.pipeline.event_graph.ancore_smistamento import (
    ESPRESSIONE_INCERTI_PREFIX,
    EVENTO,
    STAGE,
    ancora_precisa,
    scarta_contenitori_sterili,
    smista_eventi,
    user_smistamento_finestra,
)
from app.pipeline.event_graph.infra import bus as event_graph_bus
from app.pipeline.event_graph.zona_segmentation import Zona

_ETICHETTE_VIETATE = set(NATURA_ANCORE) | set(TIPI_ANCORA)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
MODULE_PATH = PACKAGE_DIR / "ancore_smistamento.py"


def _ancora(**kwargs) -> AncoraTemporaleProposta:
    kwargs.setdefault("natura", "esplicita")
    kwargs.setdefault("tipo", "data")
    return AncoraTemporaleProposta(**kwargs)


def _evento(
    eid: str,
    *,
    lemma: str = "accadere",
    span: str = "",
    posizione_doc: int = 0,
    tempo_assoluto: str | None = None,
    fuso_in: str | None = None,
    offset_inizio: int | None = None,
    offset_fine: int | None = None,
    documento: str = "doc-1",
) -> EventoRisolto:
    return EventoRisolto(
        id=eid,
        lemma=lemma,
        span=span or lemma,
        posizione_doc=posizione_doc,
        tempo_assoluto=tempo_assoluto,
        fuso_in=fuso_in,
        offset_inizio=offset_inizio,
        offset_fine=offset_fine,
        documento=documento,
    )


def _linea_carol():
    return costruisci_linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
            _ancora(
                etichetta="sera",
                inizio="1843-12-24T18",
                granularita="ora",
                padre="24 dic",
                tipo="ora",
            ),
        ],
        documento="doc-1",
    )


def _linea_intervallo():
    return costruisci_linea(
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
        documento="doc-1",
    )


def _per_etichetta(ancore: list[AncoraTemporaleProposta]) -> dict[str, AncoraTemporaleProposta]:
    return {a.etichetta: a for a in ancore}


def _foglie(ancore: list[AncoraTemporaleProposta]) -> set[str]:
    figli = {a.padre for a in ancore if a.padre}
    return {a.etichetta for a in ancore if a.etichetta not in figli}


def _figlio_incerti(
    ancore: list[AncoraTemporaleProposta], padre: str
) -> AncoraTemporaleProposta:
    return next(
        a
        for a in ancore
        if a.padre == padre
        and (a.espressione or "").startswith(ESPRESSIONE_INCERTI_PREFIX)
    )


def _membership(esito) -> dict[str, str]:
    return {item.evento_id: item.etichetta_foglia for item in esito.appartenenze}


def _assert_copertura(esito, eventi: list[EventoRisolto]) -> None:
    attesi = {e.id for e in eventi if e.id and not e.fuso_in}
    trovati = [item.evento_id for item in esito.appartenenze]
    assert esito.n_non_collocati == 0
    assert set(trovati) == attesi
    assert len(trovati) == len(attesi)
    foglie = _foglie(esito.linea.ancore)
    per = _per_etichetta(esito.linea.ancore)
    for item in esito.appartenenze:
        assert item.etichetta_foglia in foglie
        assert item.evento_id in per[item.etichetta_foglia].eventi
        for ancora in esito.linea.ancore:
            if ancora.etichetta == item.etichetta_foglia:
                continue
            assert item.evento_id not in ancora.eventi


def _zona(
    testo: str,
    *,
    offset_inizio: int = 0,
    zona_id: str = "z-1",
    documento: str = "doc-1",
    ordinale: int = 0,
) -> Zona:
    return Zona(
        id=zona_id,
        documento=documento,
        offset_inizio=offset_inizio,
        offset_fine=offset_inizio + len(testo),
        ordinale=ordinale,
        testo=testo,
    )


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


async def _boom_se_chiamato(*_args, **_kwargs):
    raise AssertionError("call_structured must not run")


@pytest.mark.asyncio
async def test_evento_datato_nella_foglia_sera_non_nel_padre_1843():
    linea = _linea_carol()
    evento = _evento(
        "e-sera",
        lemma="arrivare",
        span="arrivò la sera",
        posizione_doc=12,
        tempo_assoluto="1843-12-24T18",
    )
    esito = await smista_eventi(linea, [evento], call_structured=_boom_se_chiamato)
    per = _per_etichetta(esito.linea.ancore)
    assert _membership(esito)["e-sera"] == "sera"
    assert "e-sera" in per["sera"].eventi
    assert "e-sera" not in per["1843"].eventi
    assert "e-sera" not in per["24 dic"].eventi
    assert "1843" in per
    assert per["sera"].padre == "24 dic"
    _assert_copertura(esito, [evento])


@pytest.mark.asyncio
async def test_evento_datato_nella_foglia_intervallo_fra_due_giorni():
    linea = _linea_intervallo()
    intervallo = next(a for a in linea.ancore if a.natura == "intervallo")
    evento = _evento(
        "e-gap",
        lemma="attendere",
        span="il giorno di mezzo",
        posizione_doc=20,
        tempo_assoluto="1843-12-25",
    )
    esito = await smista_eventi(linea, [evento], call_structured=_boom_se_chiamato)
    per = _per_etichetta(esito.linea.ancore)
    intervallo_vivo = per[intervallo.etichetta]
    incerti = _figlio_incerti(esito.linea.ancore, intervallo.etichetta)
    assert _membership(esito)["e-gap"] == incerti.etichetta
    assert intervallo.etichetta in per
    assert intervallo_vivo.natura == "intervallo"
    assert intervallo_vivo.eventi == []
    assert "e-gap" in incerti.eventi
    assert incerti.padre == intervallo.etichetta
    assert "24 dic" not in per
    assert "26 dic" not in per
    assert "1843" in per
    _assert_copertura(esito, [evento])


@pytest.mark.asyncio
async def test_llm_durante_va_sulla_foglia_senza_blob_di_documento():
    linea = _linea_carol()
    blob = "UNRELATED_DOCUMENT_BLOB_" + ("MARLEY was dead. " * 400)
    assert len(blob) > 2000
    testo_zona = "Scrooge chiuse il negozio."
    zona_a = _zona(testo_zona, offset_inizio=0, zona_id="z-a", ordinale=0)
    zona_b = _zona(blob, offset_inizio=10_000, zona_id="z-b", ordinale=1)
    evento = _evento(
        "e-undated",
        lemma="chiudere",
        span="chiuse il negozio",
        posizione_doc=4,
        offset_inizio=8,
        offset_fine=26,
    )
    captured: list[str] = []

    async def spy(system_prompt, user_prompt, response_model, temperature=0, job_id=None):
        captured.append(user_prompt)
        return LivelloAncoreResult(
            segnali=[
                SegnaleAncoraEvento(
                    evento_id="e-undated",
                    ancora="sera",
                    posizione="durante",
                    stimato=True,
                )
            ]
        )

    esito = await smista_eventi(
        linea,
        [evento],
        zone=[zona_a, zona_b],
        call_structured=spy,
    )
    assert captured
    assert len(captured) == 1
    prompt = captured[0]
    assert "UNRELATED_DOCUMENT_BLOB_" not in prompt
    assert blob[:80] not in prompt
    assert "chiudere" in prompt or "e-undated" in prompt
    assert "sera" in prompt
    assert _membership(esito)["e-undated"] == "sera"
    per = _per_etichetta(esito.linea.ancore)
    assert "e-undated" in per["sera"].eventi
    assert "e-undated" not in per["1843"].eventi
    _assert_copertura(esito, [evento])


@pytest.mark.asyncio
async def test_llm_prima_di_24_dic_crea_foglia_aperta():
    linea = costruisci_linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
        ],
        documento="doc-1",
    )
    evento = _evento(
        "e-prima",
        lemma="ricordare",
        span="prima del 24 dicembre",
        posizione_doc=2,
    )

    async def spy(*_args, **_kwargs):
        return LivelloAncoreResult(
            segnali=[
                SegnaleAncoraEvento(
                    evento_id="e-prima",
                    ancora="24 dic",
                    posizione="prima",
                    stimato=True,
                )
            ]
        )

    esito = await smista_eventi(linea, [evento], call_structured=spy)
    aperte = [a for a in esito.linea.ancore if a.natura == "aperta"]
    assert len(aperte) == 1
    aperta = aperte[0]
    assert aperta.etichetta.startswith("prima")
    assert aperta.padre == "1843"
    incerti = _figlio_incerti(esito.linea.ancore, aperta.etichetta)
    assert _membership(esito)["e-prima"] == incerti.etichetta
    assert "e-prima" in incerti.eventi
    assert aperta.eventi == []
    foglie = _foglie(esito.linea.ancore)
    assert incerti.etichetta in foglie
    assert aperta.etichetta not in foglie
    assert aperta.etichetta not in _ETICHETTE_VIETATE
    _assert_copertura(esito, [evento])


@pytest.mark.asyncio
async def test_eccezione_llm_lascia_evento_senza_appartenenza_e_pubblica():
    linea = _linea_carol()
    evento = _evento("e-fail", lemma="dire", span="disse", posizione_doc=12)
    job_id = "job-smista-fail"
    queue = await event_graph_bus.subscribe(job_id)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("llm down")

    try:
        esito = await smista_eventi(
            linea, [evento], job_id=job_id, call_structured=boom
        )
        assert esito is not None
        assert esito.appartenenze == []
        assert "e-fail" not in _membership(esito)
        assert esito.n_llm_fail >= 1
        assert esito.n_non_collocati == 1
        assert all(a.etichetta not in _ETICHETTE_VIETATE for a in esito.linea.ancore)


@pytest.mark.asyncio
async def test_tempo_mention_colloca_senza_llm():
    linea = costruisci_linea(
        [
            _ancora(
                etichetta="12 marzo 1987",
                espressione="12 marzo 1987",
                inizio="1987-03-12",
                granularita="giorno",
            )
        ],
        documento="doc-1",
    )
    menzione = MenzioneRisolta(id="m-data", forma="12 marzo 1987")
    evento = EventoRisolto(
        id="e-mecc",
        lemma="trovare",
        span="Un meccanico trova un'auto",
        argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-data")],
    )
    esito = await smista_eventi(
        linea,
        [evento],
        menzioni=[menzione],
        call_structured=_boom_se_chiamato,
    )
    per = _per_etichetta(esito.linea.ancore)
    incerti = _figlio_incerti(esito.linea.ancore, "12 marzo 1987")
    assert _membership(esito)["e-mecc"] == incerti.etichetta
    assert "e-mecc" in incerti.eventi
    assert per["12 marzo 1987"].eventi == []
    assert incerti.padre == "12 marzo 1987"
    assert esito.n_datati == 1
    assert esito.appartenenze[0].base == "tempo"
        eventi = []
        while True:
            try:
                eventi.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        riepiloghi = [item for item in eventi if item["event"] == EVENTO]
        assert riepiloghi
        payload = riepiloghi[0]["payload"]
        assert riepiloghi[0]["stage"] == STAGE
        assert payload["n_eventi"] == 1
        assert payload["n_datati"] == 0
        assert payload["n_llm"] == 1
        assert payload["n_llm_fail"] >= 1
        assert payload["n_non_collocati"] == 1
    finally:
        await event_graph_bus.unsubscribe(job_id, queue)
        event_graph_bus.reset_event_bus()


@pytest.mark.asyncio
async def test_evento_senza_collocazione_resta_senza_appartenenza():
    evento = _evento("e-sfuso", lemma="soffiare", span="soffia il vento")

    async def vuoto(*_args, **_kwargs):
        return LivelloAncoreResult()

    esito = await smista_eventi(LineaAncore(), [evento], call_structured=vuoto)
    assert esito.linea.ancore == []
    assert esito.appartenenze == []
    assert esito.n_non_collocati == 1
    assert esito.n_eventi == 1


def test_riserva_etichetta_vuota_non_diventa_natura_o_tipo():
    nome = smistamento_mod._riserva_etichetta("", set())
    assert nome not in _ETICHETTE_VIETATE


@pytest.mark.asyncio
async def test_evento_fuso_saltato():
    linea = _linea_carol()
    vivo = _evento(
        "e-vivo",
        lemma="entrare",
        posizione_doc=12,
        tempo_assoluto="1843-12-24T18",
    )
    fuso = _evento(
        "e-fuso",
        lemma="fondersi",
        posizione_doc=13,
        tempo_assoluto="1843-12-24T18",
        fuso_in="e-vivo",
    )
    esito = await smista_eventi(
        linea, [vivo, fuso], call_structured=_boom_se_chiamato
    )
    ids = {item.evento_id for item in esito.appartenenze}
    assert "e-vivo" in ids
    assert "e-fuso" not in ids
    per = _per_etichetta(esito.linea.ancore)
    assert "e-fuso" not in per["sera"].eventi
    _assert_copertura(esito, [vivo, fuso])


@pytest.mark.asyncio
async def test_contenitore_sterile_scartato_padre_vivo_conservato():
    linea = costruisci_linea(
        [
            _ancora(etichetta="1843", inizio="1843", granularita="anno"),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
            ),
            _ancora(
                etichetta="sera",
                inizio="1843-12-24T18",
                granularita="ora",
                padre="24 dic",
                tipo="ora",
            ),
            _ancora(etichetta="vuoto", tipo="simbolica"),
            _ancora(etichetta="nonno sterile", tipo="epoca"),
            _ancora(
                etichetta="padre sterile",
                tipo="epoca",
                padre="nonno sterile",
            ),
        ],
        documento="doc-1",
    )
    evento = _evento(
        "e-sera",
        lemma="arrivare",
        posizione_doc=12,
        tempo_assoluto="1843-12-24T18",
    )
    esito = await smista_eventi(linea, [evento], call_structured=_boom_se_chiamato)
    etichette = {a.etichetta for a in esito.linea.ancore}
    assert "vuoto" not in etichette
    assert "nonno sterile" not in etichette
    assert "padre sterile" not in etichette
    assert "1843" in etichette
    assert "24 dic" in etichette
    assert "sera" in etichette
    per = _per_etichetta(esito.linea.ancore)
    assert per["24 dic"].padre == "1843"
    assert per["sera"].padre == "24 dic"
    assert esito.n_sterili >= 1
    _assert_copertura(esito, [evento])


def test_scarta_contenitori_sterili_diretto():
    vivi = [
        _ancora(etichetta="1843", inizio="1843", granularita="anno"),
        _ancora(
            etichetta="24 dic",
            inizio="1843-12-24",
            granularita="giorno",
            padre="1843",
            eventi=["e-0"],
        ),
        _ancora(etichetta="vuoto", inizio="1850", granularita="anno"),
    ]
    potata = scarta_contenitori_sterili(vivi)
    etichette = [a.etichetta for a in potata]
    assert etichette == ["1843", "24 dic"]
    vuoti = [
        _ancora(etichetta="nonno", tipo="epoca", granularita="secolo"),
        _ancora(
            etichetta="padre",
            tipo="epoca",
            granularita="anno",
            padre="nonno",
        ),
    ]
    assert scarta_contenitori_sterili(vuoti) == []


@pytest.mark.asyncio
async def test_ogni_evento_non_fuso_ha_esattamente_una_foglia():
    linea = _linea_carol()
    eventi = [
        _evento(
            "e-sera",
            lemma="arrivare",
            posizione_doc=12,
            tempo_assoluto="1843-12-24T18",
        ),
        _evento("e-detto", lemma="dire", span="disse qualcosa", posizione_doc=8),
        _evento(
            "e-fuso",
            lemma="fondersi",
            posizione_doc=13,
            tempo_assoluto="1843-12-24T18",
            fuso_in="e-sera",
        ),
    ]

    async def spy(*_args, **_kwargs):
        return LivelloAncoreResult(
            segnali=[
                SegnaleAncoraEvento(
                    evento_id="e-detto",
                    ancora="24 dic",
                    posizione="durante",
                )
            ]
        )

    esito = await smista_eventi(linea, eventi, call_structured=spy)
    _assert_copertura(esito, eventi)
    foglie = _foglie(esito.linea.ancore)
    assert _membership(esito)["e-detto"] in foglie
    assert _membership(esito)["e-sera"] == "sera"
    assert all(a.etichetta not in _ETICHETTE_VIETATE for a in esito.linea.ancore)


@pytest.mark.asyncio
async def test_tempo_mention_colloca_senza_llm():
    linea = costruisci_linea(
        [
            _ancora(
                etichetta="12 marzo 1987",
                espressione="12 marzo 1987",
                inizio="1987-03-12",
                granularita="giorno",
            )
        ],
        documento="doc-1",
    )
    menzione = MenzioneRisolta(id="m-data", forma="12 marzo 1987")
    evento = EventoRisolto(
        id="e-mecc",
        lemma="trovare",
        span="Un meccanico trova un'auto",
        argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-data")],
    )
    esito = await smista_eventi(
        linea,
        [evento],
        menzioni=[menzione],
        call_structured=_boom_se_chiamato,
    )
    assert _membership(esito)["e-mecc"] == "12 marzo 1987"
    assert esito.n_datati == 1
    assert esito.appartenenze[0].base == "tempo"


def test_prompt_finestra_non_include_testo_lungo():
    ancore = [_ancora(etichetta="1843", inizio="1843", granularita="anno")]
    eventi = [_evento("e-1", lemma="andare", span="andò", posizione_doc=1)]
    prompt = user_smistamento_finestra(ancore, eventi, finestra_id="z-a")
    assert "1843" in prompt
    assert "e-1" in prompt
    assert "andare" in prompt
    assert "MARLEY" not in prompt


@pytest.mark.asyncio
async def test_evento_vago_nell_intervallo_va_nel_primo_contenitore_verticale():
    linea = _linea_intervallo()
    evento_anno = _evento(
        "e-anno",
        lemma="accadere",
        span="nel 1843 accadde questo",
        posizione_doc=0,
        tempo_assoluto="1843",
    )
    evento_giorno = _evento(
        "e-vigilia",
        lemma="chiudere",
        span="chiuse il 24",
        posizione_doc=10,
        tempo_assoluto="1843-12-24",
    )
    esito = await smista_eventi(
        linea,
        [evento_anno, evento_giorno],
        call_structured=_boom_se_chiamato,
    )
    per = _per_etichetta(esito.linea.ancore)
    figli_1843 = [a for a in esito.linea.ancore if a.padre == "1843"]
    assert figli_1843
    incerti = next(
        a
        for a in figli_1843
        if (a.espressione or "").startswith(ESPRESSIONE_INCERTI_PREFIX)
    )
    assert incerti.tipo == "vaga"
    assert incerti.stimato is True
    assert incerti.etichetta.replace("\u2060", "") == "1843"
    assert figli_1843[0].etichetta == incerti.etichetta
    assert _membership(esito)["e-anno"] == incerti.etichetta
    assert "e-anno" in per[incerti.etichetta].eventi
    incerti_giorno = _figlio_incerti(esito.linea.ancore, "24 dic")
    assert _membership(esito)["e-vigilia"] == incerti_giorno.etichetta
    assert "e-vigilia" in incerti_giorno.eventi
    assert per["24 dic"].eventi == []
    assert "e-anno" not in per["24 dic"].eventi
    _assert_copertura(esito, [evento_anno, evento_giorno])


def test_ancora_precisa_solo_con_data_e_orario():
    assert ancora_precisa(
        _ancora(
            etichetta="sera",
            inizio="1843-12-24T18",
            granularita="ora",
            tipo="ora",
        )
    )
    assert ancora_precisa(
        _ancora(
            etichetta="12 marzo 1987, 08:15",
            inizio="1987-03-12T08:15",
            granularita="minuto",
        )
    )
    assert not ancora_precisa(
        _ancora(etichetta="1843", inizio="1843", granularita="anno")
    )
    assert not ancora_precisa(
        _ancora(
            etichetta="12 marzo 1987",
            inizio="1987-03-12",
            granularita="giorno",
        )
    )
    assert not ancora_precisa(_ancora(etichetta="ore 8", tipo="ora", granularita="ora"))
    assert not ancora_precisa(
        _ancora(
            etichetta="entro due giorni",
            tipo="scadenza",
            espressione="entro due giorni",
        )
    )
    assert not ancora_precisa(
        _ancora(
            etichetta="1843",
            inizio="1843-12-24T18",
            granularita="anno",
        )
    )


@pytest.mark.asyncio
async def test_orario_senza_data_espande_in_verticale():
    linea = costruisci_linea(
        [_ancora(etichetta="ore 8", tipo="ora", granularita="ora")],
        documento="doc-1",
    )
    menzione = MenzioneRisolta(id="m-ora", forma="ore 8")
    evento = EventoRisolto(
        id="e-ora",
        lemma="aprire",
        span="alle ore 8 aprì",
        argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-ora")],
    )
    esito = await smista_eventi(
        linea,
        [evento],
        menzioni=[menzione],
        call_structured=_boom_se_chiamato,
    )
    per = _per_etichetta(esito.linea.ancore)
    incerti = _figlio_incerti(esito.linea.ancore, "ore 8")
    assert per["ore 8"].padre is None
    assert per["ore 8"].eventi == []
    assert incerti.padre == "ore 8"
    assert _membership(esito)["e-ora"] == incerti.etichetta
    _assert_copertura(esito, [evento])


@pytest.mark.asyncio
async def test_orario_senza_data_va_vago_all_inizio_dell_intervallo():
    linea = costruisci_linea(
        [
            _ancora(
                etichetta="1843",
                inizio="1843",
                granularita="anno",
                posizione_doc_min=0,
                offset_inizio=0,
            ),
            _ancora(
                etichetta="24 dic",
                inizio="1843-12-24",
                granularita="giorno",
                padre="1843",
                posizione_doc_min=10,
                offset_inizio=10,
            ),
            _ancora(
                etichetta="ore 8",
                tipo="ora",
                granularita="ora",
                posizione_doc_min=20,
                offset_inizio=20,
            ),
        ],
        documento="doc-1",
    )
    menzione = MenzioneRisolta(id="m-ora", forma="ore 8")
    evento = EventoRisolto(
        id="e-ora",
        lemma="aprire",
        span="alle ore 8 aprì",
        posizione_doc=20,
        offset_inizio=20,
        offset_fine=26,
        argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-ora")],
        documento="doc-1",
    )
    esito = await smista_eventi(
        linea,
        [evento],
        menzioni=[menzione],
        call_structured=_boom_se_chiamato,
    )
    per = _per_etichetta(esito.linea.ancore)
    incerti = _figlio_incerti(esito.linea.ancore, "24 dic")
    assert "ore 8" not in per
    assert per["24 dic"].padre == "1843"
    assert per["24 dic"].eventi == []
    assert incerti.padre == "24 dic"
    assert _membership(esito)["e-ora"] == incerti.etichetta
    assert "e-ora" in incerti.eventi
    assert "e-ora" not in per["1843"].eventi
    _assert_copertura(esito, [evento])


@pytest.mark.asyncio
async def test_data_e_orario_resta_sulla_foglia_di_linea():
    linea = costruisci_linea(
        [
            _ancora(
                etichetta="12 marzo 1987, 08:15",
                espressione="12 marzo 1987, ore 08:15",
                inizio="1987-03-12T08:15",
                granularita="minuto",
            )
        ],
        documento="doc-1",
    )
    evento = _evento(
        "e-punto",
        lemma="trovare",
        span="alle 08:15 del 12 marzo",
        tempo_assoluto="1987-03-12T08:15",
    )
    esito = await smista_eventi(linea, [evento], call_structured=_boom_se_chiamato)
    per = _per_etichetta(esito.linea.ancore)
    assert _membership(esito)["e-punto"] == "12 marzo 1987, 08:15"
    assert "e-punto" in per["12 marzo 1987, 08:15"].eventi
    assert per["12 marzo 1987, 08:15"].padre is None
    assert not any(
        (a.espressione or "").startswith(ESPRESSIONE_INCERTI_PREFIX)
        for a in esito.linea.ancore
    )
    _assert_copertura(esito, [evento])


@pytest.mark.asyncio
async def test_orologio_non_matcha_data_che_contiene_la_stessa_ora():
    linea = costruisci_linea(
        [
            _ancora(
                etichetta="14 aprile 2018, ore 20:15",
                espressione="14 aprile 2018, ore 20:15",
                inizio="2018-04-14T20:15",
                granularita="minuto",
                offset_inizio=0,
                posizione_doc_min=0,
            ),
            _ancora(
                etichetta="ore 20:15",
                espressione="ore 20:15",
                tipo="ora",
                inizio="1995-03-18T20:15",
                granularita="minuto",
                offset_inizio=100,
                posizione_doc_min=100,
            ),
        ],
        documento="doc-1",
    )
    menzione = MenzioneRisolta(id="m-ora", forma="ore 20:15")
    evento = EventoRisolto(
        id="e-sfilata",
        lemma="raggiungere",
        offset_inizio=100,
        posizione_doc=100,
        argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-ora")],
    )
    esito = await smista_eventi(
        linea,
        [evento],
        menzioni=[menzione],
        call_structured=_boom_se_chiamato,
    )
    assert _membership(esito)["e-sfilata"] == "ore 20:15"


@pytest.mark.asyncio
async def test_due_orologi_uguali_vanno_all_occorrenza_piu_vicina():
    linea = costruisci_linea(
        [
            _ancora(
                etichetta="ore 16:30",
                espressione="ore 16:30",
                tipo="ora",
                inizio="1998-05-05T16:30",
                granularita="minuto",
                offset_inizio=10,
                posizione_doc_min=10,
            ),
            _ancora(
                etichetta="ore 16:30 (1978-09-16T16:30)",
                espressione="ore 16:30",
                tipo="ora",
                inizio="1978-09-16T16:30",
                granularita="minuto",
                offset_inizio=200,
                posizione_doc_min=200,
            ),
        ],
        documento="doc-1",
    )
    menzione = MenzioneRisolta(id="m-ora", forma="ore 16:30")
    evento = EventoRisolto(
        id="e-partita",
        lemma="terminare",
        offset_inizio=200,
        posizione_doc=200,
        argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-ora")],
    )
    esito = await smista_eventi(
        linea,
        [evento],
        menzioni=[menzione],
        call_structured=_boom_se_chiamato,
    )
    assert _membership(esito)["e-partita"] == "ore 16:30 (1978-09-16T16:30)"


def test_isolamento_d6():
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE_PATH))
    violations = [
        f"{MODULE_PATH}: {module}"
        for module in _import_modules(tree)
        if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "app.core" not in source
    assert "ClusterTemporaleProposto" not in source
    compact = source.replace("\n", " ")
    assert "except Exception: return None" not in compact
    assert "PRECEDE" not in source
    assert "CONTEMPORANEO" not in source
    assert "_garantisci_foglia" not in source
    assert "collocazione-ignota" not in source
