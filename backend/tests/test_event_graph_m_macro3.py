"""M-macro3 via principale tests (no Docker, no LLM)."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.pipeline.event_graph.via_principale import (
    VIA_NOMI,
    ViaPrincipale,
    annota_vie,
    calcola_vie,
)
from app.pipeline.event_graph.zona_edges import ArcoZona
from app.pipeline.event_graph.zona_segmentation import Zona

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
VIA_PATH = PACKAGE_DIR / "via_principale.py"
ZONA_PATH = PACKAGE_DIR / "zona_segmentation.py"
EDGES_PATH = PACKAGE_DIR / "zona_edges.py"


def _zona(
    ordinale: int,
    *,
    entita_principali: list[str] | None = None,
    ancore_temporali: list[str] | None = None,
    **overrides,
) -> Zona:
    payload = {
        "id": f"z-{ordinale}",
        "documento": "doc-macro3",
        "offset_inizio": ordinale * 100,
        "offset_fine": ordinale * 100 + 10,
        "ordinale": ordinale,
        "testo": f"zona {ordinale}",
        "entita_principali": list(entita_principali or []),
        "ancore_temporali": list(ancore_temporali or []),
    }
    payload.update(overrides)
    return Zona(**payload)


def _arco(da: int | str, a: int | str, tipo: str, confidenza: float = 0.9) -> ArcoZona:
    da_id = da if isinstance(da, str) else f"z-{da}"
    a_id = a if isinstance(a, str) else f"z-{a}"
    return ArcoZona(da_id=da_id, a_id=a_id, tipo=tipo, confidenza=confidenza)


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


def test_calcola_vie_always_returns_three_keys():
    empty = calcola_vie([], [])
    assert set(empty) == set(VIA_NOMI)
    assert empty["conseguenze"].zona_ids == []
    assert empty["protagonista"].zona_ids == []
    assert empty["cronologia"].zona_ids == []

    zone = [_zona(0), _zona(1), _zona(2)]
    filled = calcola_vie(zone, [])
    assert set(filled) == {"conseguenze", "protagonista", "cronologia"}
    assert all(isinstance(filled[nome], ViaPrincipale) for nome in VIA_NOMI)
    assert all(filled[nome].nome == nome for nome in VIA_NOMI)


def test_conseguenze_linear_causa_visits_all_three():
    zone = [_zona(0), _zona(1), _zona(2)]
    archi = [_arco(0, 1, "CAUSA", 0.9), _arco(1, 2, "CAUSA", 0.85)]
    vie = calcola_vie(zone, archi)
    assert vie["conseguenze"].zona_ids == ["z-0", "z-1", "z-2"]
    assert vie["conseguenze"].arco_keys == [
        ("z-0", "z-1", "CAUSA"),
        ("z-1", "z-2", "CAUSA"),
    ]


def test_conseguenze_empty_without_causal_temporal_arcs():
    zone = [_zona(0), _zona(1), _zona(2)]
    archi = [_arco(0, 1, "COLLEGATO", 0.9), _arco(1, 2, "CONTRASTO", 0.8)]
    vie = calcola_vie(zone, archi)
    assert vie["conseguenze"].zona_ids == []
    assert vie["conseguenze"].arco_keys == []


def test_protagonista_includes_zones_sharing_marco():
    zone = [
        _zona(0, entita_principali=["Marco"]),
        _zona(1, entita_principali=["pioggia"]),
        _zona(2, entita_principali=["Marco"]),
    ]
    archi = [
        _arco(0, 1, "COLLEGATO", 0.6),
        _arco(1, 2, "COLLEGATO", 0.6),
        _arco(0, 2, "COLLEGATO", 0.7),
    ]
    vie = calcola_vie(zone, archi)
    assert "z-0" in vie["protagonista"].zona_ids
    assert "z-2" in vie["protagonista"].zona_ids
    assert vie["protagonista"].zona_ids[0] == "z-0"
    assert vie["protagonista"].zona_ids[-1] == "z-2"


def test_protagonista_empty_when_no_entity_recurs():
    zone = [
        _zona(0, entita_principali=["Marco"]),
        _zona(1, entita_principali=["Anna"]),
        _zona(2, entita_principali=["Paolo"]),
    ]
    archi = [_arco(0, 1, "COLLEGATO"), _arco(1, 2, "COLLEGATO")]
    vie = calcola_vie(zone, archi)
    assert vie["protagonista"].zona_ids == []
    assert vie["protagonista"].arco_keys == []


def test_cronologia_follows_precede_chain():
    zone = [_zona(0), _zona(1), _zona(2)]
    archi = [_arco(0, 1, "PRECEDE", 0.8), _arco(1, 2, "PRECEDE", 0.8)]
    vie = calcola_vie(zone, archi)
    assert vie["cronologia"].zona_ids == ["z-0", "z-1", "z-2"]
    assert vie["cronologia"].arco_keys == [
        ("z-0", "z-1", "PRECEDE"),
        ("z-1", "z-2", "PRECEDE"),
    ]


def test_zone_on_two_vies_gets_both_names():
    zone = [
        _zona(0, entita_principali=["Marco"], ancore_temporali=["ieri"]),
        _zona(1, entita_principali=["Anna"]),
        _zona(2, entita_principali=["Marco"], ancore_temporali=["oggi"]),
    ]
    archi = [
        _arco(0, 1, "CAUSA", 0.9),
        _arco(1, 2, "CAUSA", 0.9),
        _arco(0, 1, "PRECEDE", 0.8),
        _arco(1, 2, "PRECEDE", 0.8),
        _arco(0, 2, "COLLEGATO", 0.7),
    ]
    vie = calcola_vie(zone, archi)
    assert vie["conseguenze"].zona_ids == ["z-0", "z-1", "z-2"]
    assert "z-0" in vie["protagonista"].zona_ids
    assert "z-2" in vie["protagonista"].zona_ids
    assert vie["cronologia"].zona_ids == ["z-0", "z-1", "z-2"]

    annotated_zone, annotated_archi = annota_vie(zone, archi, vie)
    z0 = next(item for item in annotated_zone if item.id == "z-0")
    z2 = next(item for item in annotated_zone if item.id == "z-2")
    assert "conseguenze" in z0.su_via_principale
    assert "protagonista" in z0.su_via_principale
    assert "cronologia" in z0.su_via_principale
    assert "conseguenze" in z2.su_via_principale
    assert "protagonista" in z2.su_via_principale
    assert len(z0.su_via_principale) == len(set(z0.su_via_principale))

    causa_01 = next(
        item
        for item in annotated_archi
        if (item.da_id, item.a_id, item.tipo) == ("z-0", "z-1", "CAUSA")
    )
    precede_01 = next(
        item
        for item in annotated_archi
        if (item.da_id, item.a_id, item.tipo) == ("z-0", "z-1", "PRECEDE")
    )
    assert "conseguenze" in causa_01.su_via_principale
    assert "cronologia" in precede_01.su_via_principale


def test_annota_vie_computes_when_vie_omitted():
    zone = [_zona(0), _zona(1)]
    archi = [_arco(0, 1, "CAUSA", 0.95)]
    out_zone, out_archi = annota_vie(zone, archi)
    assert "conseguenze" in out_zone[0].su_via_principale
    assert "conseguenze" in out_zone[1].su_via_principale
    assert "conseguenze" in out_archi[0].su_via_principale
    assert zone[0].su_via_principale == []
    assert archi[0].su_via_principale == []


def test_zona_and_arco_default_su_via_principale_empty():
    zone = _zona(0)
    arco = _arco(0, 1, "COLLEGATO")
    assert zone.su_via_principale == []
    assert arco.su_via_principale == []


def test_via_principale_is_pure_function_no_llm():
    assert inspect.iscoroutinefunction(calcola_vie) is False
    assert inspect.iscoroutinefunction(annota_vie) is False
    source = VIA_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(VIA_PATH))
    modules = _import_modules(tree)
    assert "call_structured" not in source
    assert "openai" not in source.lower()
    assert all("openai" not in module.lower() for module in modules)
    assert all("call_structured" not in module for module in modules)
    assert all(
        module != "app.pipeline.event_graph.infra.llm"
        and not module.startswith("app.pipeline.event_graph.infra.llm.")
        for module in modules
    )


def test_via_principale_isolation_ast():
    source = VIA_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(VIA_PATH))
    modules = _import_modules(tree)
    violations = [module for module in modules if _is_forbidden_import(module)]
    assert violations == []
    assert all("app.core" not in module for module in modules)
    assert all("sentence_transformers" not in module for module in modules)
    assert all("nltk" not in module for module in modules)
    assert "openai" not in modules

    zona_src = ZONA_PATH.read_text(encoding="utf-8")
    edges_src = EDGES_PATH.read_text(encoding="utf-8")
    assert "su_via_principale" in zona_src
    assert "su_via_principale" in edges_src
