"""MT3 — identity and normalisation of ancore (no Docker, no LLM)."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from app.models.event_graph import AncoraTemporaleProposta, LivelloAncoreResult
from app.pipeline.event_graph.ancore_identita import (
    EVENTO,
    STAGE,
    esegui_ancore_identita,
    identita_ancora,
    normalizza_ancore,
)
from app.pipeline.event_graph.infra import bus as event_graph_bus
from app.pipeline.event_graph.tempo_iso import normalizza as normalizza_iso

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
MODULE_PATH = PACKAGE_DIR / "ancore_identita.py"


def _ancora(**kwargs) -> AncoraTemporaleProposta:
    kwargs.setdefault("natura", "esplicita")
    return AncoraTemporaleProposta(**kwargs)


def _firma(ancore: list[AncoraTemporaleProposta]) -> list[tuple]:
    return sorted(
        (
            a.etichetta,
            a.natura,
            a.tipo,
            a.inizio,
            a.espressione,
            a.stimato,
            a.granularita,
            tuple(a.eventi),
            a.padre,
        )
        for a in ancore
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


def test_ieri_con_riferimento_diventa_giorno_stimato():
    ieri = _ancora(etichetta="ieri", tipo="relativa", espressione="ieri")
    out = normalizza_ancore([ieri], documento="doc-1", riferimento="1843-12-24")
    assert len(out) == 1
    assert out[0].inizio == "1843-12-23"
    assert out[0].stimato is True
    assert out[0].espressione == "ieri"
    assert out[0].granularita == "giorno"


def test_senza_riferimento_relativa_resta_senza_iso():
    ieri = _ancora(etichetta="ieri", tipo="relativa", espressione="ieri")
    out = normalizza_ancore([ieri], documento="doc-1")
    assert len(out) == 1
    assert out[0].inizio is None
    assert out[0].tipo == "vaga"
    assert out[0].espressione == "ieri"
    assert out[0].stimato is False


def test_fusione_stessa_collocazione_iso_giorni_diversi_no():
    giorno = _ancora(etichetta="vigilia", tipo="data", inizio="1843-12-24")
    mezzanotte = _ancora(etichetta="mezzanotte", tipo="data", inizio="1843-12-24T00:00")
    stesso = normalizza_iso("1843-12-24", None)
    altro = normalizza_iso("1843-12-24T00:00", None)
    fusi = normalizza_ancore([giorno, mezzanotte], documento="doc-1")
    if stesso == altro:
        assert len(fusi) == 1
    else:
        assert len(fusi) == 2

    varianti = normalizza_ancore(
        [
            _ancora(etichetta="a", tipo="data", inizio="1843-12-24T00:00"),
            _ancora(etichetta="b", tipo="data", inizio="1843-12-24 00:00"),
            _ancora(etichetta="c", tipo="data", inizio="1843-12-24T00:00Z"),
        ],
        documento="doc-1",
    )
    assert len(varianti) == 1
    assert varianti[0].inizio == "1843-12-24T00:00"

    diversi = normalizza_ancore(
        [
            _ancora(etichetta="24", tipo="data", inizio="1843-12-24"),
            _ancora(etichetta="25", tipo="data", inizio="1843-12-25"),
        ],
        documento="doc-1",
    )
    assert len(diversi) == 2


def test_due_natale_undated_fondono_natale_vs_1843_no():
    natale_a = _ancora(etichetta="Natale", tipo="simbolica", espressione="Natale")
    natale_b = _ancora(etichetta="natale", tipo="simbolica", espressione="Natale")
    anno = _ancora(etichetta="1843", tipo="data", inizio="1843", espressione="1843")
    out = normalizza_ancore([natale_a, natale_b, anno], documento="doc-1")
    assert len(out) == 2
    etichette = {item.etichetta.casefold() for item in out}
    assert "natale" in etichette
    assert any(item.inizio == "1843" for item in out)
    assert sum(1 for item in out if item.inizio is None) == 1


def test_etichette_univoche_dopo_due_etichette_identiche():
    a = _ancora(
        etichetta="sera",
        tipo="data",
        inizio="1843-12-24",
        posizione_doc_min=0,
    )
    b = _ancora(
        etichetta="sera",
        tipo="data",
        inizio="1843-12-25",
        posizione_doc_min=10,
    )
    out = normalizza_ancore([a, b], documento="doc-1")
    etichette = [item.etichetta for item in out]
    assert len(etichette) == 2
    assert len(set(etichette)) == 2
    assert all(len(item) <= 40 for item in etichette)
    assert "sera" in etichette


def test_stesso_input_stesso_insieme_fuso():
    ancore = [
        _ancora(
            etichetta="1843-12-24",
            tipo="data",
            inizio="1843-12-24",
            espressione="1843-12-24",
            eventi=["e-1"],
            posizione_doc_min=0,
        ),
        _ancora(
            etichetta="la vigilia",
            tipo="data",
            inizio="1843-12-24",
            espressione="24 dicembre",
            eventi=["e-2"],
            posizione_doc_min=4,
        ),
        _ancora(
            etichetta="ieri",
            tipo="relativa",
            espressione="ieri",
            posizione_doc_min=20,
        ),
        _ancora(
            etichetta="Natale",
            tipo="simbolica",
            espressione="Natale",
            posizione_doc_min=8,
        ),
        _ancora(
            etichetta="natale",
            tipo="simbolica",
            espressione="Natale",
            posizione_doc_min=9,
        ),
    ]
    primo = normalizza_ancore(ancore, documento="doc-1", riferimento="1843-12-24")
    secondo = normalizza_ancore(
        list(reversed(ancore)), documento="doc-1", riferimento="1843-12-24"
    )
    assert _firma(primo) == _firma(secondo)
    ids_primo = sorted(identita_ancora(item, "doc-1") for item in primo)
    ids_secondo = sorted(identita_ancora(item, "doc-2") for item in secondo)
    assert ids_primo == sorted(identita_ancora(item, "doc-1") for item in secondo)
    assert ids_primo != ids_secondo


def test_fusione_document_local_non_attraversa_documenti():
    ancora = _ancora(etichetta="1843", tipo="data", inizio="1843")
    copia = _ancora(etichetta="l'anno 1843", tipo="data", inizio="1843")
    nello_stesso = normalizza_ancore([ancora, copia], documento="doc-a")
    assert len(nello_stesso) == 1
    da_a = normalizza_ancore([ancora], documento="doc-a")
    da_b = normalizza_ancore([ancora], documento="doc-b")
    assert identita_ancora(da_a[0], "doc-a") != identita_ancora(da_b[0], "doc-b")
    assert identita_ancora(nello_stesso[0], "doc-a") == identita_ancora(da_a[0], "doc-a")


def test_riferimento_da_ancora_datata_precedente():
    dated = _ancora(
        etichetta="Natale",
        tipo="data",
        inizio="1843-12-24",
        stimato=False,
        posizione_doc_min=0,
        offset_inizio=0,
    )
    ieri = _ancora(
        etichetta="ieri",
        tipo="relativa",
        espressione="ieri",
        posizione_doc_min=10,
        offset_inizio=40,
    )
    out = normalizza_ancore([ieri, dated], documento="doc-1")
    risolto = next(item for item in out if item.espressione == "ieri")
    assert risolto.inizio == "1843-12-23"
    assert risolto.stimato is True


def test_tre_giorni_dopo_e_giorno_dopo():
    ref = _ancora(
        etichetta="24 dic",
        tipo="data",
        inizio="1843-12-24",
        posizione_doc_min=0,
    )

    def _risolvi(espressione: str) -> AncoraTemporaleProposta:
        rel = _ancora(
            etichetta=espressione,
            tipo="relativa",
            espressione=espressione,
            posizione_doc_min=5,
        )
        out = normalizza_ancore([ref, rel], documento="doc-1")
        return next(item for item in out if item.espressione == espressione)

    tre = _risolvi("tre giorni dopo")
    later = _risolvi("3 days later")
    giorno = _risolvi("il giorno dopo")
    sera = _risolvi("la sera dopo")
    assert tre.inizio == "1843-12-27"
    assert later.inizio == "1843-12-27"
    assert giorno.inizio == "1843-12-25"
    assert sera.inizio == "1843-12-25"
    assert tre.stimato is True
    assert giorno.stimato is True
    fusi = normalizza_ancore(
        [
            ref,
            _ancora(
                etichetta="tre giorni dopo",
                tipo="relativa",
                espressione="tre giorni dopo",
                posizione_doc_min=5,
            ),
            _ancora(
                etichetta="3 days later",
                tipo="relativa",
                espressione="3 days later",
                posizione_doc_min=8,
            ),
        ],
        documento="doc-1",
    )
    assert sum(1 for item in fusi if item.inizio == "1843-12-27") == 1


def test_offset_ore_minuti_settimane_mesi():
    ref = _ancora(
        etichetta="24 dic",
        tipo="data",
        inizio="1843-12-24",
        posizione_doc_min=0,
    )

    def _risolvi(espressione: str) -> AncoraTemporaleProposta:
        rel = _ancora(
            etichetta=espressione,
            tipo="relativa",
            espressione=espressione,
            posizione_doc_min=5,
        )
        out = normalizza_ancore([ref, rel], documento="doc-1")
        return next(item for item in out if item.espressione == espressione)

    ore = _risolvi("tra 3 ore")
    assert ore.inizio == "1843-12-24T03"
    assert ore.granularita == "ora"
    assert ore.stimato is True

    minuti = _risolvi("due minuti dopo")
    assert minuti.inizio == "1843-12-24T00:02"
    assert minuti.granularita == "minuto"

    settimane = _risolvi("due settimane dopo")
    assert settimane.inizio == "1844-01-07"
    assert settimane.granularita == "giorno"

    mese = _risolvi("il mese seguente")
    assert mese.inizio == "1844-01-24"
    assert mese.granularita == "giorno"
    assert mese.stimato is True


def test_offset_direzione_passata():
    ref = _ancora(
        etichetta="24 dic",
        tipo="data",
        inizio="1843-12-24",
        posizione_doc_min=0,
    )
    rel = _ancora(
        etichetta="tre giorni prima",
        tipo="relativa",
        espressione="tre giorni prima",
        posizione_doc_min=5,
    )
    out = normalizza_ancore([ref, rel], documento="doc-1")
    prima = next(item for item in out if item.espressione == "tre giorni prima")
    assert prima.inizio == "1843-12-21"
    assert prima.granularita == "giorno"
    assert prima.stimato is True


def test_relativa_senza_riferimento_datato_non_inventa_inizio():
    rel = _ancora(
        etichetta="due settimane dopo",
        tipo="relativa",
        espressione="due settimane dopo",
    )
    out = normalizza_ancore([rel], documento="doc-1")
    assert len(out) == 1
    assert out[0].inizio is None
    assert out[0].tipo == "vaga"
    assert out[0].espressione == "due settimane dopo"
    assert out[0].stimato is False


def test_natale_simbolica_senza_iso_non_inventa_calendario():
    natale = _ancora(etichetta="Natale", tipo="simbolica", espressione="Natale")
    out = normalizza_ancore([natale], documento="doc-1", riferimento="1843-12-24")
    assert len(out) == 1
    assert out[0].inizio is None
    assert out[0].tipo == "vaga"


def test_padre_punta_a_etichetta_che_esiste_dopo_univoche():
    padre = _ancora(etichetta="1843", tipo="data", inizio="1843", posizione_doc_min=0)
    figlio_a = _ancora(
        etichetta="sera",
        tipo="data",
        inizio="1843-12-24T18",
        padre="1843",
        posizione_doc_min=1,
    )
    figlio_b = _ancora(
        etichetta="sera",
        tipo="data",
        inizio="1843-12-25T18",
        padre="1843",
        posizione_doc_min=2,
    )
    out = normalizza_ancore([padre, figlio_a, figlio_b], documento="doc-1")
    nomi = {item.etichetta for item in out}
    assert len(nomi) == 3
    for item in out:
        if item.padre:
            assert item.padre in nomi


def test_livello_ancore_result_come_ingresso():
    livello = LivelloAncoreResult(
        ancore=[
            _ancora(etichetta="Natale", tipo="simbolica", espressione="Natale"),
            _ancora(etichetta="natale", tipo="simbolica", espressione="Natale"),
        ]
    )
    out = normalizza_ancore(livello, documento="doc-1")
    assert len(out) == 1


async def test_esegui_pubblica_riepilogo_sul_bus():
    job_id = "job-ancore-identita"
    queue = await event_graph_bus.subscribe(job_id)
    ieri = _ancora(
        etichetta="ieri",
        tipo="relativa",
        espressione="ieri",
        posizione_doc_min=10,
    )
    dated = _ancora(
        etichetta="24",
        tipo="data",
        inizio="1843-12-24",
        posizione_doc_min=0,
    )
    try:
        out = await esegui_ancore_identita(
            [ieri, dated, dated.model_copy()],
            documento="doc-1",
            job_id=job_id,
        )
        assert out
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
        assert payload["n_resolved_relative"] == 1
        assert payload["n_fuse"] >= 1
        assert payload["n_out"] == len(out)
    finally:
        await event_graph_bus.unsubscribe(job_id, queue)
        event_graph_bus.reset_event_bus()


def test_orario_eredita_il_giorno_del_riferimento_precedente():
    dated = _ancora(
        etichetta="12 marzo 1987, ore 08:15",
        tipo="data",
        inizio="1987-03-12T08:15",
        granularita="minuto",
        posizione_doc_min=0,
    )
    clock = _ancora(
        etichetta="ore 11:20",
        tipo="ora",
        espressione="ore 11:20",
        posizione_doc_min=10,
    )
    out = normalizza_ancore([dated, clock], documento="doc-1")
    ore = next(a for a in out if a.espressione == "ore 11:20")
    assert ore.inizio == "1987-03-12T11:20"
    assert ore.granularita == "minuto"
    assert ore.stimato is True
    assert ore.tipo == "ora"


def test_orario_dopo_mezzanotte_passa_al_giorno_successivo():
    sera = _ancora(
        etichetta="4 luglio 1997, ore 21:45",
        tipo="data",
        inizio="1997-07-04T21:45",
        granularita="minuto",
        posizione_doc_min=0,
    )
    clock = _ancora(
        etichetta="ore 00:30",
        tipo="ora",
        espressione="ore 00:30",
        posizione_doc_min=5,
    )
    out = normalizza_ancore([sera, clock], documento="doc-1")
    ore = next(a for a in out if a.espressione == "ore 00:30")
    assert ore.inizio == "1997-07-05T00:30"


def test_stesso_orario_su_giorni_diversi_non_fonde():
    primo = _ancora(
        etichetta="12 marzo 1987, ore 08:15",
        tipo="data",
        inizio="1987-03-12T08:15",
        granularita="minuto",
        posizione_doc_min=0,
    )
    ore_a = _ancora(
        etichetta="ore 16:30",
        tipo="ora",
        espressione="ore 16:30",
        posizione_doc_min=10,
    )
    secondo = _ancora(
        etichetta="7 giugno 1991, ore 14:00",
        tipo="data",
        inizio="1991-06-07T14:00",
        granularita="ora",
        posizione_doc_min=20,
    )
    ore_b = _ancora(
        etichetta="ore 16:30",
        tipo="ora",
        espressione="ore 16:30",
        posizione_doc_min=30,
    )
    out = normalizza_ancore([primo, ore_a, secondo, ore_b], documento="doc-1")
    orari = [a for a in out if a.espressione == "ore 16:30"]
    assert {a.inizio for a in orari} == {"1987-03-12T16:30", "1991-06-07T16:30"}


def test_orario_a_parole_eredita_il_giorno():
    dated = _ancora(
        etichetta="12 marzo 1987, ore 08:15",
        tipo="data",
        inizio="1987-03-12T08:15",
        granularita="minuto",
        posizione_doc_min=0,
    )
    clock = _ancora(
        etichetta="alle undici e venti",
        tipo="ora",
        espressione="alle undici e venti",
        posizione_doc_min=10,
    )
    out = normalizza_ancore([dated, clock], documento="doc-1")
    ore = next(a for a in out if a.espressione == "alle undici e venti")
    assert ore.inizio == "1987-03-12T11:20"
    assert ore.stimato is True


def test_orario_senza_riferimento_datato_non_inventa_il_giorno():
    clock = _ancora(
        etichetta="ore 11:20",
        tipo="ora",
        espressione="ore 11:20",
    )
    out = normalizza_ancore([clock], documento="doc-1")
    assert len(out) == 1
    assert out[0].inizio is None
    assert out[0].tipo == "ora"


def test_orario_non_eredita_un_anno_senza_giorno():
    anno = _ancora(
        etichetta="1843",
        tipo="data",
        inizio="1843",
        granularita="anno",
        posizione_doc_min=0,
    )
    clock = _ancora(
        etichetta="ore 8",
        tipo="ora",
        espressione="ore 8",
        posizione_doc_min=1,
    )
    out = normalizza_ancore([anno, clock], documento="doc-1")
    ore = next(a for a in out if a.espressione == "ore 8")
    assert ore.inizio is None


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
    compacted = source.replace("\n", " ")
    assert "except Exception:" not in compacted
    assert "except Exception as" not in compacted
    assert "foresta_temporale" not in source
    assert "ClusterTemporale" not in source
    assert "pipeline.py" not in source


def test_ancore_estrazione_resta_importabile():
    from app.pipeline.event_graph import ancore_estrazione

    assert hasattr(ancore_estrazione, "estrai_ancore")
    assert hasattr(ancore_estrazione, "prepass_regex")
