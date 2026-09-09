"""M16: backend reduced to the event-graph branch (main.py + isolation)."""

from __future__ import annotations

import ast
from pathlib import Path

from starlette.middleware.cors import CORSMiddleware

BACKEND = Path(__file__).resolve().parents[1]
MAIN_PATH = BACKEND / "app" / "main.py"

FORBIDDEN_ROUTER_INCLUDES = (
    "app.include_router(documents.router)",
    "app.include_router(dreaming.router)",
    "app.include_router(events.router)",
    "app.include_router(health.router)",
    "app.include_router(node_graph.router)",
    "app.include_router(metagraph.router)",
    "app.include_router(node_query.router)",
)

LEGACY_FILES = (
    BACKEND / "app" / "api" / "documents.py",
    BACKEND / "app" / "core" / "event_bus.py",
    BACKEND / "app" / "pipeline" / "ingestion.py",
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


def _is_app_core_import(module: str) -> bool:
    return module == "app.core" or module.startswith("app.core.")


def _iter_routes(routes) -> list:
    found: list = []
    for route in routes:
        found.append(route)
        nested = getattr(route, "routes", None)
        original = getattr(route, "original_router", None)
        if original is not None:
            nested = getattr(original, "routes", nested)
        if nested:
            found.extend(_iter_routes(nested))
    return found


def _route_paths() -> set[str]:
    from app.main import app

    paths: set[str] = set()
    for route in _iter_routes(app.routes):
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
    return paths


def test_main_source_has_no_legacy_wiring():
    source = MAIN_PATH.read_text(encoding="utf-8")
    assert "app.core" not in source
    assert "app.db.schema" not in source
    for include in FORBIDDEN_ROUTER_INCLUDES:
        assert include not in source


def test_main_imports_event_graph_driver_schema_and_router():
    source = MAIN_PATH.read_text(encoding="utf-8")
    assert "init_event_graph_driver" in source
    assert "close_event_graph_driver" in source
    assert "get_driver" in source
    assert "ensure_event_graph_schema" in source
    assert "app.include_router(event_graph_api.router)" in source
    assert "from app.api import event_graph as event_graph_api" in source
    assert "from app.pipeline.event_graph.infra.driver import" in source
    assert "from app.pipeline.event_graph.infra.schema_bootstrap import" in source


def test_event_graph_health_present_legacy_health_absent():
    paths = _route_paths()
    assert "/event-graph/health" in paths
    assert "/health" not in paths


def test_cors_middleware_still_present():
    from app.main import app

    assert any(m.cls is CORSMiddleware for m in app.user_middleware)
    source = MAIN_PATH.read_text(encoding="utf-8")
    assert "CORSMiddleware" in source
    assert "settings.CORS_ORIGINS" in source


def test_legacy_files_still_exist_on_disk():
    for path in LEGACY_FILES:
        assert path.is_file(), f"legacy file missing: {path}"


def test_main_isolation_does_not_import_app_core():
    source = MAIN_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MAIN_PATH))
    violations = [
        module for module in _import_modules(tree) if _is_app_core_import(module)
    ]
    assert violations == []
    assert "app.core" not in source
