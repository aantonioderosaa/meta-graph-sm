"""M6 mention_coref tests (no Docker, no live LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import (
    ArgomentoRisolto,
    ChunkFactsheet,
    EventoRisolto,
    MenzioneRisolta,
    SottoGrafo,
)
from app.pipeline.event_graph.ids import menzione_id
from app.pipeline.event_graph.mention_coref import (
    fondi_nomi_propri_vs_persistente,
    risolvi_intra,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
COREF_PATH = PACKAGE_DIR / "mention_coref.py"


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.runs = []

    async def run(self, query, parameters=None, **params):
        merged = dict(params)
        if parameters is not None and isinstance(parameters, dict):
            merged = {**parameters, **merged}
        self.runs.append((query, merged))

        class R:
            def data(self_inner):
                return self.rows

        return R()


def _fs() -> ChunkFactsheet:
    return ChunkFactsheet(eventi=[], archi=[], quarantena=[])


def _menzione(
    forma: str,
    tipo_superficiale: str,
    *,
    numero: str = "ignoto",
    genere: str = "ignoto",
    indice: int = 0,
    documento: str = "doc-m6",
    chunk_id: str = "chunk-m6",
) -> MenzioneRisolta:
    forma_canonica = (forma or "").strip()
    minted = menzione_id(
        forma_canonica, tipo_superficiale, documento, chunk_id, indice
    )
    return MenzioneRisolta(
        id=minted.id,
        forma=forma,
        forma_canonica=forma_canonica,
        numero=numero,  # type: ignore[arg-type]
        genere=genere,  # type: ignore[arg-type]
        tipo_superficiale=tipo_superficiale,  # type: ignore[arg-type]
        non_risolto=minted.non_risolto,
        documento=documento,
        chunk_id=chunk_id,
    )


def _evento(
    lemma: str,
    frase_indice: int,
    ruoli: list[tuple[str, MenzioneRisolta]],
    *,
    posizione_chunk: int | None = None,
    posizione_doc: int | None = None,
    event_id: str | None = None,
) -> EventoRisolto:
    return EventoRisolto(
        id=event_id or lemma,
        lemma=lemma,
        frase_indice=frase_indice,
        posizione_chunk=posizione_chunk if posizione_chunk is not None else frase_indice,
        posizione_doc=posizione_doc if posizione_doc is not None else 0,
        argomenti=[
            ArgomentoRisolto(ruolo=ruolo, menzione_id=mention.id)  # type: ignore[arg-type]
            for ruolo, mention in ruoli
        ],
    )


def _seed(sotto: SottoGrafo, *menzioni: MenzioneRisolta) -> None:
    for mention in menzioni:
        sotto.menzioni[mention.id] = mention


def test_nome_proprio_identico_dopo_normalizzazione():
    mario = _menzione("Mario Rossi", "nome_proprio", indice=0)
    mario_lc = _menzione("mario rossi", "nome_proprio", indice=1)
    assert mario.id == mario_lc.id
    assert mario.non_risolto is False
    e1 = _evento("arrivare", 0, [("SOGG", mario)])
    e2 = _evento("parlare", 1, [("SOGG", mario_lc)])
    sotto = SottoGrafo()
    _seed(sotto, mario, mario_lc)

    risolvi_intra([e1, e2], _fs(), sotto)

    assert e1.argomenti[0].menzione_id == e2.argomenti[0].menzione_id
    assert e1.argomenti[0].menzione_id == mario.id
    assert len(sotto.menzioni) == 1
    assert mario.id in sotto.menzioni


def test_sottostringa_token_aware_canonica_piu_lunga():
    mario = _menzione("Mario", "nome_proprio", indice=0)
    rossi = _menzione("Mario Rossi", "nome_proprio", indice=1)
    e1 = _evento("arrivare", 0, [("SOGG", mario)], posizione_chunk=0, event_id="e-mario")
    sotto = SottoGrafo()
    _seed(sotto, mario)
    risolvi_intra([e1], _fs(), sotto)
    sotto.eventi.append(e1)

    e2 = _evento("parlare", 1, [("SOGG", rossi)], posizione_chunk=0, posizione_doc=1, event_id="e-rossi")
    _seed(sotto, rossi)
    risolvi_intra([e2], _fs(), sotto)

    assert e1.argomenti[0].menzione_id == rossi.id
    assert e2.argomenti[0].menzione_id == rossi.id
    assert rossi.id in sotto.menzioni
    assert mario.id not in sotto.menzioni

    mar = _menzione("Mar", "nome_proprio", indice=2)
    mario2 = _menzione("Mario", "nome_proprio", indice=3)
    e3 = _evento("vedere", 0, [("SOGG", mar), ("OGG", mario2)], event_id="e-mar")
    sotto_token = SottoGrafo()
    _seed(sotto_token, mar, mario2)
    risolvi_intra([e3], _fs(), sotto_token)
    assert e3.argomenti[0].menzione_id == mar.id
    assert e3.argomenti[1].menzione_id == mario2.id
    assert mar.id in sotto_token.menzioni
    assert mario2.id in sotto_token.menzioni


def test_pronome_un_antecedente_compatibile():
    mario = _menzione("Mario", "nome_proprio", numero="sing", genere="masc", indice=0)
    lui = _menzione("lui", "pronome", numero="sing", genere="masc", indice=1)
    e1 = _evento("arrivare", 0, [("SOGG", mario)])
    e2 = _evento("parlare", 1, [("SOGG", lui)])
    sotto = SottoGrafo()
    _seed(sotto, mario, lui)

    risolvi_intra([e1, e2], _fs(), sotto)

    assert e2.argomenti[0].menzione_id == mario.id
    assert lui.id not in sotto.menzioni
    assert mario.id in sotto.menzioni


def test_pronome_due_antecedenti_compatibili_non_risolto():
    mario = _menzione("Mario", "nome_proprio", numero="sing", genere="masc", indice=0)
    luigi = _menzione("Luigi", "nome_proprio", numero="sing", genere="masc", indice=1)
    lui = _menzione("lui", "pronome", numero="sing", genere="masc", indice=2)
    e1 = _evento("arrivare", 0, [("SOGG", mario)])
    e2 = _evento("sedere", 0, [("SOGG", luigi)])
    e3 = _evento("parlare", 1, [("SOGG", lui)])
    sotto = SottoGrafo()
    _seed(sotto, mario, luigi, lui)

    risolvi_intra([e1, e2, e3], _fs(), sotto)

    assert e3.argomenti[0].menzione_id == lui.id
    assert lui.non_risolto is True
    assert lui.id in sotto.menzioni
    assert lui.id != mario.id
    assert lui.id != luigi.id


def test_pronome_zero_antecedenti_non_risolto():
    lui = _menzione("lui", "pronome", numero="sing", genere="masc", indice=0)
    e1 = _evento("parlare", 0, [("SOGG", lui)])
    sotto = SottoGrafo()
    _seed(sotto, lui)

    risolvi_intra([e1], _fs(), sotto)

    assert e1.argomenti[0].menzione_id == lui.id
    assert lui.non_risolto is True
    assert lui.id in sotto.menzioni


def test_sogg_nullo_sogg_precedente_unico_o_doppio():
    mario = _menzione("Mario", "nome_proprio", numero="sing", genere="masc", indice=0)
    nullo = _menzione("", "sogg_nullo", numero="sing", genere="masc", indice=1)
    e1 = _evento("arrivare", 0, [("SOGG", mario)])
    e2 = _evento("parlare", 1, [("SOGG", nullo)])
    sotto = SottoGrafo()
    _seed(sotto, mario, nullo)
    risolvi_intra([e1, e2], _fs(), sotto)
    assert e2.argomenti[0].menzione_id == mario.id
    assert nullo.id not in sotto.menzioni

    anna = _menzione("Anna", "nome_proprio", numero="sing", genere="femm", indice=0)
    luigi = _menzione("Luigi", "nome_proprio", numero="sing", genere="masc", indice=1)
    nullo2 = _menzione("", "sogg_nullo", numero="ignoto", genere="ignoto", indice=2)
    e3 = _evento("arrivare", 0, [("SOGG", anna)], event_id="e-anna")
    e4 = _evento("sedere", 0, [("SOGG", luigi)], event_id="e-luigi")
    e5 = _evento("partire", 1, [("SOGG", nullo2)], event_id="e-nullo2")
    sotto2 = SottoGrafo()
    _seed(sotto2, anna, luigi, nullo2)
    risolvi_intra([e3, e4, e5], _fs(), sotto2)
    assert e5.argomenti[0].menzione_id == nullo2.id
    assert nullo2.non_risolto is True
    assert nullo2.id in sotto2.menzioni


def test_sn_comune_identico_fonde():
    cane1 = _menzione("il cane", "sn_comune", indice=0)
    cane2 = _menzione("il cane", "sn_comune", indice=1)
    assert cane1.id == cane2.id
    assert cane1.non_risolto is False
    e1 = _evento("abbaiare", 0, [("SOGG", cane1)])
    e2 = _evento("correre", 1, [("SOGG", cane2)])
    sotto = SottoGrafo()
    _seed(sotto, cane1, cane2)

    risolvi_intra([e1, e2], _fs(), sotto)

    assert e1.argomenti[0].menzione_id == e2.argomenti[0].menzione_id
    assert e1.argomenti[0].menzione_id == cane1.id
    assert cane1.non_risolto is False
    assert cane2.non_risolto is False
    assert len(sotto.menzioni) == 1


@pytest.mark.asyncio
async def test_fondi_nomi_propri_vs_persistente_retarget():
    mario = _menzione("Mario", "nome_proprio", indice=0)
    e1 = _evento("arrivare", 0, [("SOGG", mario)])
    sotto = SottoGrafo()
    _seed(sotto, mario)
    sotto.eventi.append(e1)

    persisted_id = menzione_id("Mario Rossi", "nome_proprio", "", "", 0).id
    session = FakeSession(
        [{"id": persisted_id, "forma_canonica": "Mario Rossi"}]
    )

    await fondi_nomi_propri_vs_persistente(session, sotto)

    assert e1.argomenti[0].menzione_id == persisted_id
    assert persisted_id in sotto.menzioni
    assert mario.id not in sotto.menzioni or mario.id == persisted_id
    assert session.runs


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
    lowered = module.lower()
    if "node_resolution" in lowered:
        return True
    if "embed" in lowered:
        return True
    if "sentence_transformers" in lowered or "sentence-transformers" in lowered:
        return True
    return False


def test_mention_coref_isolation_ast():
    source = COREF_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(COREF_PATH))
    violations = [
        f"{COREF_PATH}: {module}"
        for module in _import_modules(tree)
        if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "node_resolution" not in source
    assert "app.core" not in source
    assert "sentence_transformers" not in source
    assert "sentence-transformers" not in source
