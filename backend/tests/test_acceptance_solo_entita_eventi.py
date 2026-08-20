"""Macrotask 8 acceptance: Fact layer is gone; Node/Concept graph is the product."""

from __future__ import annotations

import re
from pathlib import Path

from app.db.schema import (
    REQUIRED_BTREE_INDEXES,
    REQUIRED_CONSTRAINTS,
    REQUIRED_VECTOR_INDEXES,
    load_schema_statements,
)
from app.main import app

FACT_STAGES = frozenset(
    {"extraction", "grouping", "consolidation", "relation_detection"}
)
# origin_fact_ids is the ConnectivityRule origin list (Fase 7), not the removed :Fact layer.
FACT_RESIDUE = re.compile(r"\bFact\b|:Fact\b|(?<!origin_)fact_")
SM_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = SM_ROOT / "backend" / "app"
FRONTEND_COMPONENTS = SM_ROOT / "frontend" / "components"
FRONTEND_LIB = SM_ROOT / "frontend" / "lib"


def _openapi_paths() -> dict[str, dict[str, object]]:
    """Source of truth for registered routes.

    ``app.routes`` does not work here: with included routers, FastAPI/Starlette
    wrap them so top-level entries have no usable ``.path`` (verified against
    fastapi==0.141.1 — every route showed up as path ``""``). The OpenAPI schema
    is always the fully-resolved view, independent of how routers are nested.
    """
    return app.openapi()["paths"]


def _route_paths() -> set[str]:
    return set(_openapi_paths())


def _methods_for(path: str) -> set[str]:
    operations = _openapi_paths().get(path, {})
    return {method.upper() for method in operations}


def test_scenario_2_fact_routes_are_gone_delete_graph_remains():
    paths = _route_paths()
    assert "/query" not in paths
    assert "/queries" not in paths
    assert "/queries/{query_id}" not in paths
    assert "/facts/{fact_id}" not in paths
    assert "/facts/{fact_id}/history" not in paths
    assert "/reconcile" not in paths
    assert "GET" not in _methods_for("/graph")
    assert "DELETE" in _methods_for("/graph")
    assert "/graph/entities" in paths
    assert "/graph/events" in paths
    assert "/graph/query" in paths


def test_scenario_6_schema_has_no_fact_indexes():
    names = REQUIRED_CONSTRAINTS | REQUIRED_BTREE_INDEXES | REQUIRED_VECTOR_INDEXES
    assert not any(name.startswith("fact_") for name in names)
    assert "query_log_id" not in names
    assert "query_log_created_at" not in names
    blob = "\n".join(load_schema_statements())
    assert ":Fact" not in blob
    assert ":QueryLog" not in blob.replace(":NodeQueryLog", "")


def test_scenario_1_pipelines_never_publish_fact_stages():
    dreaming = (APP_ROOT / "pipeline" / "dreaming.py").read_text(encoding="utf-8")
    ingestion = (APP_ROOT / "pipeline" / "ingestion.py").read_text(encoding="utf-8")
    for stage in FACT_STAGES:
        assert f'"{stage}"' not in dreaming
        assert f'"{stage}"' not in ingestion
    assert '"reconciliation"' in dreaming
    assert '"done"' in dreaming
    assert '"done"' in ingestion
    assert "process_chunk_node_extraction" in ingestion


def test_scenario_2_dashboard_has_single_entity_event_explorer():
    shell = (FRONTEND_COMPONENTS / "DashboardShell.tsx").read_text(encoding="utf-8")
    assert shell.count("<EntityEventExplorer") == 1
    assert shell.count("<DomainGraphPanel") == 1
    assert "DomainDashboard" in shell
    assert "DomainDetailCard" in shell
    assert "drillPath" in shell
    assert '"dettagliata"' in shell
    assert "Vista dettagliata" in shell
    assert "Vista generale" in shell
    assert "{generale ?" in shell
    assert "GraphExplorer" not in shell
    assert 'from "@/components/QueryPanel"' not in shell
    assert "NodeQueryPanel" in shell
    assert "Fatti" not in shell
    assert "GraphViewTabs" not in shell
    assert "ConceptDomainExplorer" in shell
    assert "IdentityDetailPanel" in shell
    assert "ContradictionsPanel" in shell
    assert "ConnectivityRulesPanel" in shell
    assert "JudgeLogPanel" in shell


def test_scenario_2b_metagraph_layer_panels_exist_as_list_not_nvl():
    names = (
        "ConceptDomainExplorer.tsx",
        "IdentityDetailPanel.tsx",
        "ContradictionsPanel.tsx",
        "ConnectivityRulesPanel.tsx",
        "JudgeLogPanel.tsx",
        "BundleDetailPanel.tsx",
        "NodeMetadataPanel.tsx",
        "DomainDashboard.tsx",
        "DomainDetailCard.tsx",
    )
    for name in names:
        path = FRONTEND_COMPONENTS / name
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        assert "InteractiveNvlWrapper" not in text
        assert "GraphPanel" not in text


def test_scenario_2c_domain_graph_reuses_graph_panel_not_a_fifth_canvas():
    panel = (FRONTEND_COMPONENTS / "DomainGraphPanel.tsx").read_text(encoding="utf-8")
    assert "GraphPanel" in panel
    assert "getDomainsGraph" in panel
    assert "getDomainChildrenGraph" in panel
    assert "colorByKernelCategory" in panel
    assert "InteractiveNvlWrapper" not in panel
    shell = (FRONTEND_COMPONENTS / "DashboardShell.tsx").read_text(encoding="utf-8")
    assert "{generale ?" in shell
    assert "<DomainGraphPanel" in shell
    assert "<EntityEventExplorer" in shell
    assert "<MacroGraphPanel" not in shell


def test_scenario_3_graph_panel_handles_nvl_init_error():
    panel = (FRONTEND_COMPONENTS / "GraphPanel.tsx").read_text(encoding="utf-8")
    assert "onInitializationError" in panel
    assert "Riprova" in panel
    assert "onRelationshipClick" in panel


def test_scenario_4_include_concepts_defaults_off():
    engine = (APP_ROOT / "pipeline" / "node_graph_engine.py").read_text(
        encoding="utf-8"
    )
    api = (APP_ROOT / "api" / "node_graph.py").read_text(encoding="utf-8")
    explorer = (FRONTEND_COMPONENTS / "EntityEventExplorer.tsx").read_text(
        encoding="utf-8"
    )
    assert "include_concepts: bool = False" in engine
    assert "include_concepts: bool = Query(default=False)" in api
    assert "showEntityConcepts" in explorer
    assert "showEventConcepts" in explorer
    assert "Concetti ↔ entità" in explorer
    assert "Concetti ↔ eventi" in explorer


def test_scenario_5_reset_lives_on_node_graph():
    node_graph = (APP_ROOT / "api" / "node_graph.py").read_text(encoding="utf-8")
    assert "DETACH DELETE n" in node_graph
    assert '@router.delete("", response_model=GraphResetResponse)' in node_graph
    client = (FRONTEND_LIB / "api-client.ts").read_text(encoding="utf-8")
    assert "resetKnowledgeBase" in client
    assert '"/graph"' in client or "'/graph'" in client


def test_scenario_6_no_fact_residue_in_app_and_frontend_surfaces():
    hits: list[str] = []
    roots = (APP_ROOT, FRONTEND_COMPONENTS, FRONTEND_LIB)
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            text = path.read_text(encoding="utf-8")
            if FACT_RESIDUE.search(text):
                hits.append(str(path.relative_to(SM_ROOT)))
    assert hits == []
