"""Explicit leftover sentences vs extracted events (no LLM)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.models.event_graph import EventoRisolto, SottoGrafo
from app.pipeline.event_graph.buchi import (
    REGOLA,
    _gia_coperto,
    _norm_span,
    _span_evento,
    frasi_non_coperte,
    riempi_buchi_documento,
)
from app.pipeline.event_graph.zona_segmentation import Zona

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
BUCHI_PATH = PACKAGE_DIR / "buchi.py"
PIPELINE_PATH = PACKAGE_DIR / "pipeline.py"
SNAPSHOT_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "sole_vento_riestrazione.json"
)
WITHHELD_LEMMA = "Alla fine il Vento dovette arrendersi."


def _zona(ordinale: int, testo: str, *, doc_id: str = "doc-buchi") -> Zona:
    prefix = "".join(f"zona {i} x. " for i in range(ordinale))
    start = len(prefix)
    return Zona(
        id=f"z-{ordinale}",
        documento=doc_id,
        offset_inizio=start,
        offset_fine=start + len(testo),
        ordinale=ordinale,
        testo=testo,
        riassunto=testo[:40],
        espansa=True,
    )


def _evento(i: int, span: str, *, chunk_id: str = "z-0") -> EventoRisolto:
    return EventoRisolto(
        id=f"e-{i}",
        lemma=span,
        ancora=span,
        chunk_id=chunk_id,
        posizione_doc=i,
        posizione_chunk=i,
        offset_inizio=i * 10,
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


def _load_snapshot() -> tuple[list[Zona], list[EventoRisolto]]:
    data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    zone = [
        Zona(
            id=item["id"],
            documento=item["documento"],
            offset_inizio=int(item["offset_inizio"]),
            offset_fine=int(item["offset_fine"]),
            ordinale=int(item["ordinale"]),
            testo=item["testo"] or "",
            riassunto=item.get("riassunto") or "",
            espansa=bool(item.get("espansa")),
        )
        for item in data["zone"]
    ]
    eventi = [
        EventoRisolto(
            id=item["id"],
            lemma=item.get("lemma") or "",
            ancora=item.get("ancora") or item.get("lemma") or "",
            chunk_id=item.get("chunk_id"),
            offset_inizio=item.get("offset_inizio"),
            offset_fine=item.get("offset_fine"),
            posizione_doc=item.get("posizione_doc"),
            posizione_chunk=item.get("posizione_chunk"),
            documento=item.get("documento"),
        )
        for item in data["eventi"]
    ]
    return zone, eventi


def _split_withheld(
    eventi: list[EventoRisolto],
) -> tuple[list[EventoRisolto], EventoRisolto]:
    withheld = next(
        item for item in eventi if (item.lemma or "").strip() == WITHHELD_LEMMA
    )
    rest = [item for item in eventi if item.id != withheld.id]
    return rest, withheld


def _covers(candidate: str, target: str) -> bool:
    return _gia_coperto(candidate, {_norm_span(target)}) or _gia_coperto(
        target, {_norm_span(candidate)}
    )


def test_isolation_ast():
    source = BUCHI_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(BUCHI_PATH))
    modules = _import_modules(tree)
    assert not any(_is_forbidden_import(module) for module in modules)
    assert "call_structured" not in source
    assert all("call_structured" not in module for module in modules)


def test_pipeline_fills_buchi_before_zone_relations():
    source = PIPELINE_PATH.read_text(encoding="utf-8")
    body = source[source.index("async def run_event_graph_ingestion") :]
    assert body.index("estrai_zona(") < body.index("riempi_buchi_documento(")
    assert body.index("riempi_buchi_documento(") < body.index("collega_relazioni_zona(")
    assert body.index("collega_relazioni_zona(") < body.index(
        "collega_dorsale_eventi(sotto, zone)"
    )
    assert body.index("riempi_buchi_documento(") < body.index(
        "estrai_livello_relazioni("
    )


def test_frasi_non_coperte_finds_unextracted_sentence():
    z0 = _zona(0, "Il Sole e il Vento discussero. Il Vento soffiò con grande forza.")
    holes = frasi_non_coperte(
        [z0], [_evento(0, "Il Sole e il Vento discussero.", chunk_id="z-0")]
    )
    assert len(holes) == 1
    assert holes[0][1].testo == "Il Vento soffiò con grande forza."


def test_prefix_adverb_does_not_open_a_hole():
    z0 = _zona(0, "Un giorno il Sole e il Vento cominciarono a discutere.")
    already = "Il Sole e il Vento cominciarono a discutere."
    holes = frasi_non_coperte([z0], [_evento(0, already, chunk_id="z-0")])
    assert holes == []


@pytest.mark.asyncio
async def test_riempi_adds_missing_chunk_sentence():
    z0 = _zona(0, "Il Sole e il Vento discussero. Il Vento soffiò con grande forza.")
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=[_evento(0, "Il Sole e il Vento discussero.", chunk_id="z-0")])
    added = await riempi_buchi_documento(sotto, [z0], job_id="job-b")
    assert len(added) == 1
    assert added[0].regola == REGOLA
    assert added[0].chunk_id == "z-0"
    assert _covers(added[0].lemma or added[0].ancora or "", "Il Vento soffiò con grande forza.")


@pytest.mark.asyncio
async def test_riempi_skips_when_every_sentence_is_covered():
    z0 = _zona(0, "Il Sole e il Vento discussero.")
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=[_evento(0, "Il Sole e il Vento discussero.", chunk_id="z-0")])
    added = await riempi_buchi_documento(sotto, [z0])
    assert added == []
    assert len(sotto.eventi) == 1


@pytest.mark.asyncio
async def test_snapshot_full_inventory_has_no_holes():
    zone, eventi = _load_snapshot()
    assert len(eventi) == 23
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=list(eventi))
    added = await riempi_buchi_documento(sotto, zone)
    assert added == []
    assert frasi_non_coperte(zone, eventi) == []


@pytest.mark.asyncio
async def test_snapshot_minus_one_fills_the_withheld_sentence():
    zone, eventi = _load_snapshot()
    rest, withheld = _split_withheld(eventi)
    holes = frasi_non_coperte(zone, rest)
    assert any(_covers(span.testo, WITHHELD_LEMMA) for _zona, span in holes)
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=list(rest))
    added = await riempi_buchi_documento(sotto, zone)
    assert any(_covers(item.lemma or item.ancora or "", WITHHELD_LEMMA) for item in added)
    assert all(item.regola == REGOLA for item in added)
    assert any(item.chunk_id == withheld.chunk_id for item in added)
    assert _span_evento(withheld) == WITHHELD_LEMMA
