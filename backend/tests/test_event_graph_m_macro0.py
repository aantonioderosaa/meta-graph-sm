"""M-macro0 zone segmentation tests (no Docker, no LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

from app.pipeline.event_graph.chunking_periods import split_sentences_with_offsets
from app.pipeline.event_graph.ids import zona_id
from app.pipeline.event_graph.infra.schema_bootstrap import SCHEMA_PATH
from app.pipeline.event_graph.zona_segmentation import Zona, segmenta_zone

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
ZONA_PATH = PACKAGE_DIR / "zona_segmentation.py"

_SUN_WIND = [
    "The sun rose above the distant hills.",
    "Warm sunlight filled the windy valley.",
    "The wind blew across the sunny meadows.",
    "Solar rays heated the breeze near the dunes.",
    "A gust of wind scattered the sunlight.",
    "The shining sun warmed the western cliffs.",
    "Windy gusts swept the sunlit plains.",
    "The sun and the wind shaped the sandy dunes.",
]
_TRAIN_STATION = [
    "The locomotive entered the crowded station.",
    "Passengers boarded the waiting train.",
    "The conductor checked tickets on the platform.",
    "Railway tracks gleamed beside the depot.",
    "A whistle announced the departing train.",
    "Commuters hurried along the station corridor.",
    "The engine hissed steam near the platform.",
    "Luggage piled beside the railway carriages.",
]
_HOMOGENEOUS = (
    "The old mill stood beside the quiet river. "
    "Water flowed under the wooden mill wheel. "
    "The miller watched the river from the mill door. "
    "Grain waited near the mill stones. "
    "The river turned the mill wheel every morning. "
    "Flour filled the sacks inside the mill. "
    "The miller carried flour down to the river path. "
    "Ducks swam near the mill at dusk."
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


def test_empty_text_yields_no_zones():
    assert segmenta_zone("", "doc-empty") == []
    assert segmenta_zone("   \n\n  ", "doc-ws") == []


def test_one_sentence_one_zone_stable_id_and_offsets():
    text = "Mario arrivò alle tre."
    first = segmenta_zone(text, "doc-one")
    second = segmenta_zone(text, "doc-one")
    assert len(first) == 1
    zone = first[0]
    assert 0 <= zone.offset_inizio < zone.offset_fine <= len(text)
    assert text[zone.offset_inizio : zone.offset_fine] == zone.testo
    assert text[zone.offset_inizio : zone.offset_fine] == text
    assert zone.ordinale == 0
    assert zone.documento == "doc-one"
    assert zone.id == zona_id("doc-one", 0, zone.testo)
    assert [z.id for z in first] == [z.id for z in second]
    assert zone.riassunto == ""
    assert zone.entita_principali == []
    assert zone.ancore_temporali == []
    assert zone.evento_centrale is None
    assert zone.espansa is False
    assert zone.regola == "M0_texttiling"


def test_homogeneous_prose_is_one_zone():
    zones = segmenta_zone(_HOMOGENEOUS, "doc-homo")
    assert len(zones) == 1
    zone = zones[0]
    assert zone.ordinale == 0
    assert zone.offset_inizio == 0
    assert zone.offset_fine == len(_HOMOGENEOUS)
    assert zone.testo == _HOMOGENEOUS


def test_two_disjoint_topics_yield_two_nonoverlapping_zones():
    text = " ".join(_SUN_WIND + _TRAIN_STATION)
    zones = segmenta_zone(text, "doc-topics")
    assert len(zones) == 2
    assert [z.ordinale for z in zones] == [0, 1]
    left, right = zones
    assert left.offset_fine <= right.offset_inizio
    assert 0 <= left.offset_inizio < left.offset_fine <= len(text)
    assert 0 <= right.offset_inizio < right.offset_fine <= len(text)
    assert text[left.offset_inizio : left.offset_fine] == left.testo
    assert text[right.offset_inizio : right.offset_fine] == right.testo
    assert "sun" in left.testo.lower()
    assert "wind" in left.testo.lower()
    assert "locomotive" not in left.testo.lower()
    assert "locomotive" in right.testo.lower()
    assert "sun rose" not in right.testo.lower()
    assert left.testo.strip() != right.testo.strip()
    # Adjacent zones must not share sentence text.
    left_sents = split_sentences_with_offsets(left.testo)
    right_sents = split_sentences_with_offsets(right.testo)
    left_bodies = {s.testo.strip() for s in left_sents}
    right_bodies = {s.testo.strip() for s in right_sents}
    assert left_bodies.isdisjoint(right_bodies)
    assert left.id == zona_id("doc-topics", 0, left.testo)
    assert right.id == zona_id("doc-topics", 1, right.testo)
    assert left.id != right.id


def test_abbreviations_and_decimals_do_not_false_split():
    text = (
        "Dr. Rossi measured 3.14 units. "
        "Prof. Bianchi agreed with Dr. Rossi about the 3.14 reading."
    )
    spans = split_sentences_with_offsets(text)
    assert len(spans) == 2
    assert "Dr. Rossi" in spans[0].testo
    assert "3.14" in spans[0].testo
    assert "Prof. Bianchi" in spans[1].testo
    assert "3.14" in spans[1].testo
    for span in spans:
        assert text[span.inizio : span.fine] == span.testo
    zones = segmenta_zone(text, "doc-abbr")
    assert len(zones) == 1
    assert "Dr. Rossi" in zones[0].testo
    assert "3.14" in zones[0].testo


def test_zona_segmentation_isolation_ast():
    source = ZONA_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ZONA_PATH))
    violations = [
        module for module in _import_modules(tree) if _is_forbidden_import(module)
    ]
    assert violations == []
    modules = _import_modules(tree)
    assert all(
        "sentence_transformers" not in module and "nltk" not in module
        for module in modules
    )


def test_schema_contains_zona_constraint():
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    assert ":Zona" in raw
    assert "eg_zona_id" in raw
    assert "CREATE CONSTRAINT eg_zona_id" in raw
    assert "IF NOT EXISTS" in raw
    assert ":EgChunk" in raw
    assert ":Chunk" not in raw.replace(":EgChunk", "")
    assert "VECTOR" not in raw.upper()
    # Addendum 5: one Lucene fulltext index on :Evento (keyword/topic
    # retrieval), still no vector/ML embeddings — see test_event_graph_m1.py.
    assert "eg_evento_testo" in raw
    assert "eg_cluster_temporale_id" in raw
    assert "eg_cluster_temporale_doc" in raw
    assert ":ClusterTemporale" in raw


def test_zona_defaults_match_m0_contract():
    zone = Zona(
        id="z",
        documento="d",
        offset_inizio=0,
        offset_fine=1,
        ordinale=0,
        testo="x",
    )
    assert zone.riassunto == ""
    assert zone.entita_principali == []
    assert zone.ancore_temporali == []
    assert zone.evento_centrale is None
    assert zone.espansa is False
    assert zone.regola == "M0_texttiling"
