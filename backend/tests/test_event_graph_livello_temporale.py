"""MT6 / Parte B — document-level temporal extraction (no Docker, no live LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import (
    ArcoEvento,
    ClusterTemporaleProposto,
    EventoRisolto,
    LivelloTemporaleResult,
    SegnaleTemporaleEvento,
)
from app.pipeline.event_graph.livello_temporale import (
    CONFIDENZA_COLLOCAZIONE_INCERTA,
    LIVELLO_MAX_EVENTI_PER_CHIAMATA,
    SOGLIA_CLUSTER,
    SYSTEM_LIVELLO_TEMPORALE,
    _merge_results,
    _sanitize,
    coppie_precede_da_archi,
    estrai_livello_temporale,
    eventi_senza_collocazione,
    giustifica_iso,
    user_livello_temporale,
)
from app.pipeline.event_graph.zona_segmentation import Zona

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
LIVELLO_PATH = PACKAGE_DIR / "livello_temporale.py"


def _evento(
    i: int,
    *,
    span: str | None = None,
    lemma: str = "x",
    offset_inizio: int | None = None,
    offset_fine: int | None = None,
) -> EventoRisolto:
    return EventoRisolto(
        id=f"e-{i}",
        lemma=lemma,
        span=span if span is not None else f"evento {i}",
        posizione_doc=i,
        offset_inizio=offset_inizio,
        offset_fine=offset_fine,
    )


def _zona(
    i: int,
    *,
    inizio: int,
    fine: int,
    riassunto: str = "",
    ancore: list[str] | None = None,
    testo: str = "TESTO INTEGRALE DELLA ZONA",
) -> Zona:
    return Zona(
        id=f"z-{i}",
        documento="doc",
        offset_inizio=inizio,
        offset_fine=fine,
        ordinale=i,
        testo=testo,
        riassunto=riassunto,
        ancore_temporali=list(ancore or []),
    )


def _install_stub(monkeypatch, handler):
    async def stub(
        system_prompt,
        user_prompt,
        response_model,
        temperature=0,
        job_id=None,
    ):
        return await handler(
            system_prompt, user_prompt, response_model, temperature, job_id
        )

    monkeypatch.setattr(
        "app.pipeline.event_graph.livello_temporale.call_structured",
        stub,
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


@pytest.mark.asyncio
async def test_b_u1_tempo_assoluto_from_explicit_date(monkeypatch):
    evento = _evento(0, span="Mario arrivò nel 1990")

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        assert temperature == 0
        assert response_model is LivelloTemporaleResult
        assert "nel 1990" in user_prompt
        assert "e-0 |" in user_prompt
        return LivelloTemporaleResult(
            segnali=[
                SegnaleTemporaleEvento(evento_id="e-0", tempo_assoluto="1990"),
            ]
        )

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_temporale([evento], job_id="job-t")
    assert result is not None
    assert result.segnali
    assert result.segnali[0].evento_id == "e-0"
    assert result.segnali[0].tempo_assoluto == "1990"
    assert evento.tempo_assoluto == "1990"
    assert evento.tempo_assoluto_revisioni[-1] == "1990"


@pytest.mark.asyncio
async def test_b_u2_contemporaneo_a_populated(monkeypatch):
    eventi = [
        _evento(0, span="X camminava"),
        _evento(1, span="Y parlava"),
    ]

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        assert temperature == 0
        return LivelloTemporaleResult(
            segnali=[
                SegnaleTemporaleEvento(
                    evento_id="e-0", contemporaneo_a=["e-1"]
                ),
                SegnaleTemporaleEvento(evento_id="e-1"),
            ]
        )

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_temporale(eventi, job_id="job-t")
    assert result is not None
    populated = [s for s in result.segnali if s.contemporaneo_a]
    assert populated
    assert "e-1" in populated[0].contemporaneo_a


@pytest.mark.asyncio
async def test_b_u3_invented_evento_id_dropped(monkeypatch):
    evento = _evento(0, span="accadde nel 1990")

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return LivelloTemporaleResult(
            segnali=[
                SegnaleTemporaleEvento(
                    evento_id="ghost-99", tempo_assoluto="1990"
                ),
                SegnaleTemporaleEvento(
                    evento_id="e-0", tempo_assoluto="1990"
                ),
            ],
            cluster=[
                ClusterTemporaleProposto(
                    etichetta="1990",
                    tipo="data_esplicita",
                    eventi=["e-0", "ghost-99"],
                )
            ],
        )

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_temporale([evento])
    assert result is not None
    ids = [s.evento_id for s in result.segnali]
    assert "ghost-99" not in ids
    assert "e-0" in ids
    assert all("ghost-99" not in c.eventi for c in result.cluster)


@pytest.mark.asyncio
async def test_b_u4_windows_merged_without_duplicate_etichetta(monkeypatch):
    assert LIVELLO_MAX_EVENTI_PER_CHIAMATA == 60
    eventi = [_evento(i) for i in range(61)]
    calls: list[str] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append(user_prompt)
        n = len(calls)
        eid = "e-0" if n == 1 else "e-60"
        return LivelloTemporaleResult(
            cluster=[
                ClusterTemporaleProposto(
                    etichetta="1990",
                    tipo="data_esplicita",
                    eventi=[eid],
                )
            ]
        )

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_temporale(eventi)
    assert len(calls) >= 2
    assert result is not None
    etichette = [c.etichetta for c in result.cluster]
    assert etichette.count("1990") == 1
    membri = result.cluster[0].eventi
    assert "e-0" in membri
    assert "e-60" in membri


@pytest.mark.asyncio
async def test_b_u5_llm_failure_returns_none(monkeypatch):
    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        raise RuntimeError("llm down")

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_temporale([_evento(0)])
    assert result is None


def test_user_prompt_orders_by_exposition_and_span_fallback():
    late = EventoRisolto(id="e-late", lemma="partì", span="partì", posizione_doc=2)
    early = EventoRisolto(
        id="e-early", lemma="arrivò", ancora="nel bosco", span="", posizione_doc=0
    )
    prompt = user_livello_temporale([late, early])
    pos_early = prompt.index("e-early |")
    pos_late = prompt.index("e-late |")
    assert pos_early < pos_late
    assert "arrivò|nel bosco" in prompt
    assert "ghost" not in prompt


# --- MT3 (PIANO-LIVELLO-TEMPORALE-V2) --------------------------------------
# Collocazione sempre obbligatoria, cluster annidati, gate di confidenza,
# input = riassunti delle zone invece del testo integrale.


def test_prompt_usa_i_riassunti_delle_zone_coinvolte():
    zone = [
        _zona(
            0,
            inizio=0,
            fine=50,
            riassunto="Scrooge lavora nel suo ufficio gelido.",
            ancore=["la vigilia di Natale", "sette anni fa"],
            testo="INTEGRALE UNO",
        ),
        _zona(
            1,
            inizio=50,
            fine=120,
            riassunto="Il fantasma di Marley sale le scale.",
            testo="INTEGRALE DUE",
        ),
        _zona(
            2,
            inizio=200,
            fine=300,
            riassunto="ZONA LONTANA senza eventi nella finestra.",
            testo="INTEGRALE TRE",
        ),
    ]
    eventi = [
        _evento(0, span="Scrooge rifiuta l'invito", offset_inizio=10, offset_fine=20),
        _evento(1, span="Marley bussa", offset_inizio=60, offset_fine=70),
    ]

    prompt = user_livello_temporale(eventi, zone)

    assert "Scrooge lavora nel suo ufficio gelido." in prompt
    assert "Il fantasma di Marley sale le scale." in prompt
    assert "la vigilia di Natale" in prompt
    assert "sette anni fa" in prompt
    # il testo integrale non entra mai nel prompt: è ciò che sfondava il contesto
    assert "INTEGRALE" not in prompt
    # solo le zone attraversate dalla finestra
    assert "ZONA LONTANA" not in prompt
    assert "e-0 | zona 1 |" in prompt
    assert "e-1 | zona 2 |" in prompt


def test_prompt_degrada_senza_zone():
    prompt = user_livello_temporale([_evento(0, span="qualcosa accade")])
    assert "ZONE" not in prompt
    assert "EVENTI DA COLLOCARE (id | frase)" in prompt
    assert "e-0 | qualcosa accade" in prompt


def test_prompt_degrada_con_riassunti_vuoti_ma_tiene_le_ancore():
    zone = [
        _zona(0, inizio=0, fine=50, riassunto="", ancore=["tre giorni dopo"]),
        _zona(1, inizio=50, fine=100, riassunto="", ancore=[]),
    ]
    eventi = [
        _evento(0, span="primo", offset_inizio=10, offset_fine=20),
        _evento(1, span="secondo", offset_inizio=60, offset_fine=70),
    ]

    prompt = user_livello_temporale(eventi, zone)

    assert "tre giorni dopo" in prompt
    assert "e-0 | zona 1 | primo" in prompt
    # la seconda zona non ha né riassunto né ancore: non viene numerata, e il suo
    # evento resta nella lista senza riferimento di zona
    assert "zona 2" not in prompt
    assert "e-1 | secondo" in prompt


def test_prompt_ignora_zone_senza_offset():
    prompt = user_livello_temporale([_evento(0, span="primo")], ["non una zona"])
    assert "EVENTI DA COLLOCARE (id | frase)" in prompt
    assert "e-0 | primo" in prompt


def test_system_prompt_dichiara_la_soglia_e_il_limite_di_etichetta():
    assert SOGLIA_CLUSTER == 0.6
    assert str(SOGLIA_CLUSTER) in SYSTEM_LIVELLO_TEMPORALE
    assert "40 caratteri" in SYSTEM_LIVELLO_TEMPORALE
    assert "Non inventare date" in SYSTEM_LIVELLO_TEMPORALE
    assert "Solo relazioni DIRETTE" in SYSTEM_LIVELLO_TEMPORALE
    assert "padre" in SYSTEM_LIVELLO_TEMPORALE
    assert "UNA sola relazione fra precede e" in SYSTEM_LIVELLO_TEMPORALE
    assert "contemporaneo" in SYSTEM_LIVELLO_TEMPORALE


@pytest.mark.asyncio
async def test_collocazione_emessa_per_ogni_evento(monkeypatch):
    eventi = [_evento(i) for i in range(3)]

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return LivelloTemporaleResult(
            segnali=[
                SegnaleTemporaleEvento(
                    evento_id="e-0", tempo_assoluto="1843-12-24", granularita="giorno"
                ),
                SegnaleTemporaleEvento(
                    evento_id="e-1",
                    tempo_assoluto="1843-12-24",
                    stimato=True,
                    confidenza=0.4,
                    base="e-0",
                ),
                SegnaleTemporaleEvento(
                    evento_id="e-2", espressione_relativa="il giorno dopo"
                ),
            ]
        )

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_temporale(eventi)

    assert result is not None
    assert {s.evento_id for s in result.segnali} == {"e-0", "e-1", "e-2"}
    # e-2 ha l'espressione del testo; la ISO stimata di e-1 cade
    assert eventi_senza_collocazione(eventi, result) == ["e-1"]
    stimato = next(s for s in result.segnali if s.evento_id == "e-1")
    assert stimato.stimato is True
    assert stimato.tempo_assoluto is None
    assert stimato.confidenza == 0.4
    assert stimato.base == "e-0"


def test_eventi_senza_collocazione_segnala_i_mancanti():
    eventi = [_evento(0), _evento(1), _evento(2)]
    result = LivelloTemporaleResult(
        segnali=[SegnaleTemporaleEvento(evento_id="e-0", tempo_assoluto="1843")],
        cluster=[
            ClusterTemporaleProposto(
                etichetta="1843",
                tipo="data_esplicita",
                inizio="1843",
                eventi=["e-1"],
            )
        ],
    )
    assert eventi_senza_collocazione(eventi, result) == ["e-2"]
    assert eventi_senza_collocazione(eventi, None) == ["e-0", "e-1", "e-2"]


def test_gate_soglia_cluster_sopra_al_bordo_e_sotto():
    eventi = [_evento(0), _evento(1), _evento(2)]
    result = LivelloTemporaleResult(
        segnali=[
            SegnaleTemporaleEvento(
                evento_id="e-2", tempo_assoluto="1843", stimato=True, confidenza=0.2
            )
        ],
        cluster=[
            ClusterTemporaleProposto(
                etichetta="sopra",
                tipo="data_esplicita",
                eventi=["e-0"],
                confidenza=0.9,
            ),
            ClusterTemporaleProposto(
                etichetta="bordo",
                tipo="data_esplicita",
                eventi=["e-1"],
                confidenza=0.6,
            ),
            ClusterTemporaleProposto(
                etichetta="sotto",
                tipo="simbolico",
                eventi=["e-2"],
                confidenza=0.59,
            ),
        ],
    )

    sanitized = _sanitize(result, eventi)

    etichette = {c.etichetta for c in sanitized.cluster}
    # esattamente 0.6 raggruppa; 0.59 no, e senza membri il cluster sparisce.
    # e-2 (il suo unico membro) non aveva nessun'altra lettura: §B11 lo
    # raccoglie in un sottocluster a bassa confidenza invece di perderlo
    assert etichette == {"sopra", "bordo", "collocazione incerta — bordo"}
    assert {c.etichetta: c.eventi for c in sanitized.cluster} == {
        "sopra": ["e-0"],
        "bordo": ["e-1"],
        "collocazione incerta — bordo": ["e-2"],
    }
    incerto = next(
        c for c in sanitized.cluster if c.etichetta == "collocazione incerta — bordo"
    )
    assert incerto.padre == "bordo"
    assert incerto.confidenza == CONFIDENZA_COLLOCAZIONE_INCERTA
    # sotto soglia resta foglia; la data stimata non è una collocazione
    superstite = next(s for s in sanitized.segnali if s.evento_id == "e-2")
    assert superstite.tempo_assoluto is None
    assert superstite.stimato is True
    # "bordo" non ha un inizio reale (mai dichiarato), quindi risalendo il
    # padre del sottocluster e-2 resta comunque senza una posizione nel tempo:
    # più visibile di prima, ma ancora correttamente segnalato come mancante
    assert "e-2" in eventi_senza_collocazione(eventi, sanitized)


def test_sanitize_propaga_i_campi_nuovi_e_conserva_il_padre():
    eventi = [_evento(0), _evento(1)]
    result = LivelloTemporaleResult(
        segnali=[
            SegnaleTemporaleEvento(
                evento_id="e-0",
                tempo_assoluto="1843-12-24T18:30",
                granularita="minuto",
                stimato=True,
                confidenza=0.75,
                base="il narratore dice 'poco dopo cena'",
            )
        ],
        cluster=[
            ClusterTemporaleProposto(
                etichetta="1843",
                tipo="intervallo",
                descrizione="l'anno intero del racconto",
                granularita="anno",
                inizio="1843",
                fine="1843-12-31",
                confidenza=0.8,
            ),
            ClusterTemporaleProposto(
                etichetta="24 dic, sera",
                tipo="data_esplicita",
                descrizione="la vigilia, dopo la chiusura dell'ufficio",
                granularita="ora",
                inizio="1843-12-24T18",
                stimato=True,
                confidenza=0.7,
                padre="1843",
                eventi=["e-0", "e-1"],
            ),
        ],
    )

    sanitized = _sanitize(result, eventi)

    segnale = sanitized.segnali[0]
    assert segnale.stimato is True
    assert segnale.tempo_assoluto is None
    assert segnale.granularita is None
    assert segnale.confidenza == 0.75
    assert segnale.base == "il narratore dice 'poco dopo cena'"

    per_etichetta = {c.etichetta: c for c in sanitized.cluster}
    figlio = per_etichetta["24 dic, sera"]
    assert figlio.padre == "1843"
    assert figlio.descrizione == "la vigilia, dopo la chiusura dell'ufficio"
    assert figlio.inizio is None
    assert figlio.stimato is True
    assert figlio.confidenza == 0.7
    assert figlio.eventi == ["e-0", "e-1"]
    # il contenitore non ha eventi propri e sopravvive lo stesso, con i suoi campi
    padre = per_etichetta["1843"]
    assert padre.eventi == []
    assert padre.inizio == "1843"
    assert padre.fine == "1843-12-31"
    assert padre.descrizione == "l'anno intero del racconto"


def test_sanitize_azzera_un_padre_inesistente():
    eventi = [_evento(0)]
    result = LivelloTemporaleResult(
        cluster=[
            ClusterTemporaleProposto(
                etichetta="24 dic, sera",
                tipo="data_esplicita",
                padre="un cluster mai proposto",
                eventi=["e-0"],
            )
        ]
    )
    sanitized = _sanitize(result, eventi)
    assert len(sanitized.cluster) == 1
    assert sanitized.cluster[0].padre is None


def test_sanitize_scarta_un_contenitore_senza_figli_vivi():
    eventi = [_evento(0)]
    result = LivelloTemporaleResult(
        cluster=[
            ClusterTemporaleProposto(etichetta="vuoto", tipo="simbolico", eventi=[]),
            ClusterTemporaleProposto(
                etichetta="pieno", tipo="data_esplicita", eventi=["e-0"]
            ),
        ]
    )
    sanitized = _sanitize(result, eventi)
    assert [c.etichetta for c in sanitized.cluster] == ["pieno"]


def test_sanitize_deduce_la_granularita_mancante_da_tempo_iso():
    eventi = [_evento(0), _evento(1)]
    result = LivelloTemporaleResult(
        segnali=[
            SegnaleTemporaleEvento(evento_id="e-0", tempo_assoluto="1843-12"),
            SegnaleTemporaleEvento(evento_id="e-1", tempo_assoluto="ieri sera"),
        ],
        cluster=[
            ClusterTemporaleProposto(
                etichetta="24 dic, 18:30",
                tipo="data_esplicita",
                inizio="1843-12-24T18:30",
                eventi=["e-0"],
            )
        ],
    )

    sanitized = _sanitize(result, eventi)

    per_id = {s.evento_id: s for s in sanitized.segnali}
    assert per_id["e-0"].granularita == "mese"
    # un tempo_assoluto che non è ISO non produce una granularità inventata
    assert per_id["e-1"].granularita is None
    assert sanitized.cluster[0].granularita == "minuto"


def test_sanitize_azzera_un_inizio_non_parsabile_senza_perdere_il_cluster():
    eventi = [_evento(0)]
    result = LivelloTemporaleResult(
        cluster=[
            ClusterTemporaleProposto(
                etichetta="verso il 1840",
                tipo="relativo",
                descrizione="un momento imprecisato",
                inizio="circa 1840",
                fine="1843-99-99",
                eventi=["e-0"],
            )
        ]
    )

    sanitized = _sanitize(result, eventi)

    cluster = sanitized.cluster[0]
    assert cluster.etichetta == "verso il 1840"
    assert cluster.eventi == ["e-0"]
    assert cluster.descrizione == "un momento imprecisato"
    assert cluster.inizio is None
    assert cluster.fine is None
    assert cluster.granularita is None


def test_merge_results_propaga_i_campi_nuovi():
    prima = LivelloTemporaleResult(
        cluster=[
            ClusterTemporaleProposto(
                etichetta="24 dic, sera",
                tipo="data_esplicita",
                eventi=["e-0"],
                granularita="ora",
                stimato=True,
                confidenza=0.9,
                padre="1843",
            )
        ]
    )
    seconda = LivelloTemporaleResult(
        cluster=[
            ClusterTemporaleProposto(
                etichetta="24 dic, sera",
                tipo="data_esplicita",
                eventi=["e-1"],
                inizio="1843-12-24T18",
                descrizione="dopo la chiusura dell'ufficio",
                stimato=False,
                confidenza=0.7,
            )
        ]
    )

    merged = _merge_results([prima, seconda])

    assert len(merged.cluster) == 1
    fuso = merged.cluster[0]
    assert fuso.eventi == ["e-0", "e-1"]
    assert fuso.granularita == "ora"
    assert fuso.padre == "1843"
    # un buco della prima finestra si riempie con la seconda
    assert fuso.inizio == "1843-12-24T18"
    assert fuso.descrizione == "dopo la chiusura dell'ufficio"
    # una finestra che ha visto la collocazione dichiarata toglie la stima
    assert fuso.stimato is False
    # raggruppare richiede confidenza: vince il dubbio
    assert fuso.confidenza == 0.7


@pytest.mark.asyncio
async def test_estrai_passa_le_zone_al_prompt_e_conserva_la_gerarchia(monkeypatch):
    zone = [
        _zona(
            0,
            inizio=0,
            fine=80,
            riassunto="La vigilia, Scrooge chiude l'ufficio.",
            ancore=["24 dicembre 1843"],
        )
    ]
    eventi = [_evento(0, span="Scrooge chiude", offset_inizio=5, offset_fine=15)]
    prompts: list[str] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        prompts.append(user_prompt)
        return LivelloTemporaleResult(
            segnali=[
                SegnaleTemporaleEvento(
                    evento_id="e-0", tempo_assoluto="1843-12-24T18", stimato=True
                )
            ],
            cluster=[
                ClusterTemporaleProposto(
                    etichetta="1843", tipo="intervallo", inizio="1843"
                ),
                ClusterTemporaleProposto(
                    etichetta="24 dic, sera",
                    tipo="data_esplicita",
                    inizio="1843-12-24T18",
                    padre="1843",
                    eventi=["e-0"],
                ),
            ],
        )

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_temporale(eventi, zone, job_id="job-t")

    assert prompts
    assert "La vigilia, Scrooge chiude l'ufficio." in prompts[0]
    assert "24 dicembre 1843" in prompts[0]
    assert result is not None
    per_etichetta = {c.etichetta: c for c in result.cluster}
    assert per_etichetta["24 dic, sera"].padre == "1843"
    assert per_etichetta["1843"].granularita == "anno"
    assert eventi_senza_collocazione(eventi, result) == []


def test_livello_temporale_isolation_ast():
    source = LIVELLO_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LIVELLO_PATH))
    modules = _import_modules(tree)
    violations = [module for module in modules if _is_forbidden_import(module)]
    assert violations == []
    assert any(
        module == "app.pipeline.event_graph.infra.llm"
        or module.startswith("app.pipeline.event_graph.infra.llm.")
        for module in modules
    )
    assert all("app.core" not in module for module in modules)
    assert "openai" not in modules


def test_coppia_con_entrambi_si_risolve_con_le_finestre():
    eventi = [_evento(0), _evento(1)]
    result = LivelloTemporaleResult(
        segnali=[
            SegnaleTemporaleEvento(
                evento_id="e-0",
                tempo_assoluto="1843-12-24T18:05:30",
                granularita="minuto",
                contemporaneo_a=["e-1"],
                precede=["e-1"],
            ),
            SegnaleTemporaleEvento(
                evento_id="e-1",
                tempo_assoluto="1843-12-24T18:07:30",
                granularita="secondo",
                contemporaneo_a=["e-0"],
            ),
        ]
    )
    sanitized = _sanitize(result, eventi)
    by_id = {s.evento_id: s for s in sanitized.segnali}
    assert by_id["e-0"].precede == ["e-1"]
    assert by_id["e-0"].contemporaneo_a == []
    assert by_id["e-1"].contemporaneo_a == []
    assert by_id["e-1"].precede == []


def test_contemporaneo_cade_se_le_finestre_sono_in_ordine():
    eventi = [_evento(0), _evento(1)]
    result = LivelloTemporaleResult(
        segnali=[
            SegnaleTemporaleEvento(
                evento_id="e-0",
                tempo_assoluto="1843-12-24T18:00:00",
                granularita="ora",
                contemporaneo_a=["e-1"],
            ),
            SegnaleTemporaleEvento(
                evento_id="e-1",
                tempo_assoluto="1843-12-24T19:00:00",
                granularita="ora",
            ),
        ]
    )
    sanitized = _sanitize(result, eventi)
    by_id = {s.evento_id: s for s in sanitized.segnali}
    assert "e-1" not in by_id["e-0"].contemporaneo_a
    assert by_id["e-0"].precede == ["e-1"]


def test_precede_cede_al_contemporaneo_se_le_finestre_coincidono():
    eventi = [_evento(0), _evento(1)]
    result = LivelloTemporaleResult(
        segnali=[
            SegnaleTemporaleEvento(
                evento_id="e-0",
                tempo_assoluto="1843-12-24",
                granularita="giorno",
                precede=["e-1"],
                contemporaneo_a=["e-1"],
            ),
            SegnaleTemporaleEvento(
                evento_id="e-1",
                tempo_assoluto="1843-12-24",
                granularita="giorno",
            ),
        ]
    )
    sanitized = _sanitize(result, eventi)
    by_id = {s.evento_id: s for s in sanitized.segnali}
    assert by_id["e-0"].precede == []
    assert by_id["e-0"].contemporaneo_a == ["e-1"]


def test_precede_gia_nel_grafo_toglie_il_contemporaneo():
    eventi = [_evento(0), _evento(1)]
    result = LivelloTemporaleResult(
        segnali=[
            SegnaleTemporaleEvento(
                evento_id="e-0",
                tempo_assoluto="1843-12-24",
                contemporaneo_a=["e-1"],
            ),
            SegnaleTemporaleEvento(evento_id="e-1", tempo_assoluto="1843-12-24"),
        ]
    )
    sanitized = _sanitize(result, eventi, {frozenset(("e-0", "e-1"))})
    by_id = {s.evento_id: s for s in sanitized.segnali}
    assert by_id["e-0"].contemporaneo_a == []


def test_coppie_precede_da_archi_ignora_i_superati():
    vivi = coppie_precede_da_archi(
        [
            ArcoEvento(tipo="PRECEDE", da_id="e-0", a_id="e-1"),
            ArcoEvento(
                tipo="PRECEDE",
                da_id="e-2",
                a_id="e-3",
                props={"superato_da": "altro"},
            ),
            ArcoEvento(tipo="SEQUENZA", da_id="e-0", a_id="e-1"),
        ]
    )
    assert vivi == {frozenset(("e-0", "e-1"))}


def test_giustifica_iso_taglia_cio_che_il_testo_non_scrive():
    assert giustifica_iso("1843-12-24T18:00", "un giorno il sole") is None
    assert giustifica_iso("1843-12-24T18:00", "dicembre 1843") == "1843-12"
    assert giustifica_iso("1843-12-24T18:00", "24 dicembre 1843") == "1843-12-24"
    assert (
        giustifica_iso("1843-12-24T18:30:00", "24 dicembre 1843 alle 18:30")
        == "1843-12-24T18:30:00"
    )


def test_documento_senza_date_azzera_l_iso_inventata():
    zone = [_zona(0, inizio=0, fine=40, testo="Un giorno il Sole e il Vento discussero.")]
    eventi = [_evento(0, span="Il Sole e il Vento discussero")]
    result = LivelloTemporaleResult(
        segnali=[
            SegnaleTemporaleEvento(
                evento_id="e-0",
                tempo_assoluto="1843-12-24T18:00:00",
                stimato=False,
            )
        ],
        cluster=[
            ClusterTemporaleProposto(
                etichetta="24 dic, sera",
                tipo="data_esplicita",
                inizio="1843-12-24T18:00:00",
                eventi=["e-0"],
            )
        ],
    )
    sanitized = _sanitize(result, eventi, zone=zone)
    assert sanitized.segnali[0].tempo_assoluto is None
    assert sanitized.cluster[0].inizio is None
    assert sanitized.cluster[0].tipo == "relativo"


def test_precede_transitivo_viene_ridotto():
    eventi = [_evento(0), _evento(1), _evento(2)]
    result = LivelloTemporaleResult(
        segnali=[
            SegnaleTemporaleEvento(evento_id="e-0", precede=["e-1", "e-2"]),
            SegnaleTemporaleEvento(evento_id="e-1", precede=["e-2"]),
            SegnaleTemporaleEvento(evento_id="e-2"),
        ]
    )
    sanitized = _sanitize(result, eventi)
    by_id = {s.evento_id: s for s in sanitized.segnali}
    assert by_id["e-0"].precede == ["e-1"]
    assert by_id["e-1"].precede == ["e-2"]
    assert by_id["e-2"].precede == []
