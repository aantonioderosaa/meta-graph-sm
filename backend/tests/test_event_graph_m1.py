"""M1 foundation tests for the isolated event_graph package."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from app.models.event_graph import (
    ArcoEventoGrezzo,
    ArgomentoGrezzo,
    ChunkFactsheet,
    EventoGrezzo,
    FrammentoQuarantena,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.config import EventGraphSettings
from app.pipeline.event_graph.ids import (
    content_hash,
    eg_chunk_id,
    evento_id,
    menzione_id,
    quarantena_id,
)
from app.pipeline.event_graph.infra import bus as event_graph_bus
from app.pipeline.event_graph.infra.llm import call_structured
from app.pipeline.event_graph.infra.schema_bootstrap import (
    SCHEMA_PATH,
    load_schema_statements,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def test_ruleset_version_is_code_constant():
    assert RULESET_VERSION == "1.1.1"


def test_settings_defaults_and_distinct_class():
    from app.core.config import Settings

    assert EventGraphSettings is not Settings
    assert not issubclass(EventGraphSettings, Settings)

    fields = EventGraphSettings.model_fields
    assert fields["NEO4J_URI"].default == "bolt://localhost:7687"
    assert fields["NEO4J_USER"].default == "neo4j"
    assert fields["NEO4J_PASSWORD"].default == "changeme"
    assert fields["OPENAI_API_KEY"].default == ""
    assert fields["OPENAI_BASE_URL"].default == ""
    assert fields["OPENAI_MODEL"].default == "gpt-4o-mini"
    assert fields["EVENT_GRAPH_TARGET_CHUNK_WORDS"].default == 350
    assert fields["EVENT_GRAPH_MAX_CHUNK_WORDS"].default == 500
    assert fields["EVENT_GRAPH_LLM_TIMEOUT"].default == 300
    assert fields["EVENT_GRAPH_LLM_CONCURRENCY"].default == 4
    assert fields["EVENT_GRAPH_LLM_MAX_TOKENS"].default == 6000
    assert fields["EVENT_GRAPH_EXTRACTION_MAX_CALLS"].default == 3
    assert fields["EVENT_GRAPH_TEMPORAL_MAX_CANDIDATES"].default == 10
    assert fields["EVENT_GRAPH_FLASH_MODE"].default is False
    assert fields["CORS_ORIGINS"].default == "http://localhost:3000"


def test_chunk_factsheet_round_trip():
    factsheet = ChunkFactsheet(
        eventi=[
            EventoGrezzo(
                indice=0,
                lemma="arrivare",
                span="arrivò alle tre",
                tempo="passato",
                segmentazione="principale_finita",
                polarita_negata=False,
                modalizzato=False,
                modalizzato_forma=None,
                iterativo=False,
                ruolo_se="nessuno",
                completiva_di=None,
                classe_verbo_reggente="nessuna",
                finale=False,
                frase_tipo="dichiarativa",
                marca_dialogo=False,
                frase_indice=0,
                avverbio_temporale_esplicito=True,
                connettivo_sequenziale_esplicito=False,
                tempo_assoluto_grezzo="alle tre",
                argomenti=[
                    ArgomentoGrezzo(
                        ruolo="SOGG",
                        forma="Mario",
                        tipo_superficiale="nome_proprio",
                        span="Mario",
                    )
                ],
            )
        ],
        archi=[
            ArcoEventoGrezzo(
                da_indice=0,
                a_indice=1,
                segnale_testuale="quindi",
                relazione_segnale="consecuzione",
                orientamento="coordinata",
            )
        ],
        quarantena=[
            FrammentoQuarantena(
                frammento="…",
                motivo="malformazione",
                span="0:1",
            )
        ],
    )
    restored = ChunkFactsheet.model_validate(factsheet.model_dump())
    assert restored == factsheet
    assert restored.eventi[0].lemma == "arrivare"
    assert restored.eventi[0].argomenti[0].tipo_superficiale == "nome_proprio"
    assert restored.archi[0].relazione_segnale == "consecuzione"
    assert restored.quarantena[0].motivo == "malformazione"


def test_id_helpers_deterministic_and_stable():
    text = "Mario arrivò alle tre."
    first = evento_id("doc-a", text, 0)
    second = evento_id("doc-a", text, 0)
    assert first == second == _sha1(f"doc-a|{_sha1(text)}|0")
    assert first != evento_id("doc-a", text + " ", 0)
    assert first != evento_id("doc-a", text, 1)
    assert content_hash(text) == _sha1(text)
    assert RULESET_VERSION not in first
    assert first != _sha1(f"doc-a|{_sha1(text)}|0|{RULESET_VERSION}")

    proprio = menzione_id("Mario Rossi", "nome_proprio", "doc-a", "chunk-1", 0)
    proprio_other_doc = menzione_id("Mario Rossi", "nome_proprio", "doc-b", "chunk-9", 7)
    assert proprio.id == proprio_other_doc.id == _sha1("mario rossi")
    assert proprio.non_risolto is False
    assert RULESET_VERSION not in proprio.id

    comune = menzione_id("il cane", "sn_comune", "doc-a", "chunk-1", 0)
    comune_other_doc = menzione_id("Il Cane", "sn_comune", "doc-b", "chunk-9", 7)
    assert comune.id == comune_other_doc.id == _sha1("cane")
    assert comune.non_risolto is False

    pronome = menzione_id("egli", "pronome", "doc-a", "chunk-1", 0)
    assert pronome.non_risolto is True
    assert pronome.id == _sha1("doc-a|chunk-1|0")
    assert pronome.id != menzione_id("egli", "pronome", "doc-a", "chunk-1", 1).id

    chunk = eg_chunk_id("doc-a", 2, text)
    assert chunk == _sha1(f"doc-a|2|{_sha1(text)}")
    assert chunk != eg_chunk_id("doc-a", 2, text + "x")

    quarantena = quarantena_id("doc-a", text, "0:4", "ciclo CAUSA")
    assert quarantena == _sha1(f"doc-a|{_sha1(text)}|0:4|ciclo CAUSA")
    assert quarantena != quarantena_id("doc-a", text, "0:4", "altro")


def test_schema_cypher_constraints_indexes_and_labels():
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    constraints = [
        "eg_evento_id",
        "eg_menzione_id",
        "eg_quarantena_id",
        "eg_documento_id",
        "eg_chunk_id",
        "eg_zona_id",
        "eg_run_id",
    ]
    indexes = [
        "eg_evento_doc",
        "eg_evento_lemma",
        "eg_evento_piano",
        "eg_evento_posizione",
        "eg_evento_tempo_abs",
        "eg_evento_catena",
        "eg_menzione_forma",
        "eg_zona_doc",
        "eg_zona_offset",
    ]
    for name in constraints:
        assert name in raw
    for name in indexes:
        assert name in raw
    assert raw.count("CREATE CONSTRAINT") == 7
    assert raw.count("CREATE INDEX") == 9
    assert raw.count("IF NOT EXISTS") == 17
    assert ":EgChunk" in raw
    assert ":Zona" in raw
    assert ":Chunk" not in raw.replace(":EgChunk", "")
    assert "VECTOR" not in raw.upper()
    # Addendum 5: one Lucene fulltext index on :Evento is the deliberate,
    # scoped exception to the no-search-index rule — keyword/topic retrieval
    # for /query/structured's `testo` field. Still no vector/ML embeddings.
    assert raw.upper().count("FULLTEXT") == 1
    assert "eg_evento_testo" in raw


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


def test_package_isolation_no_legacy_imports():
    violations: list[str] = []
    for path in PACKAGE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _import_modules(tree):
            if _is_forbidden_import(module):
                violations.append(f"{path}: {module}")
    assert violations == []


@pytest.mark.asyncio
async def test_bus_subscribe_publish_unsubscribe_order():
    event_graph_bus.reset_event_bus()
    job_id = "eg-job-1"
    queue = await event_graph_bus.subscribe(job_id)
    assert event_graph_bus.subscriber_count(job_id) == 1

    await event_graph_bus.publish(job_id, "estrazione", "chunk_ready", {"n": 1})
    await event_graph_bus.publish(job_id, "regole_chunk", "rules_done", {"n": 2})
    await event_graph_bus.publish(job_id, "done", "pipeline_complete", {"n": 3})

    events = [await queue.get(), await queue.get(), await queue.get()]
    assert [item["stage"] for item in events] == [
        "estrazione",
        "regole_chunk",
        "done",
    ]
    assert [item["event"] for item in events] == [
        "chunk_ready",
        "rules_done",
        "pipeline_complete",
    ]
    assert events[0]["job_id"] == job_id
    assert "ts" in events[0]

    await event_graph_bus.unsubscribe(job_id, queue)
    assert event_graph_bus.subscriber_count(job_id) == 0
    event_graph_bus.reset_event_bus()


def test_call_structured_is_package_local():
    from app.core import llm_client as legacy_llm

    assert call_structured is not legacy_llm.call_structured
    assert call_structured.__module__ == "app.pipeline.event_graph.infra.llm"


def test_load_schema_statements_count():
    statements = load_schema_statements()
    assert len(statements) == 17
