"""Node e2e smoke: ingest narrative → dream → assert :Node exist.

Formerly Fact-based (EXTENDS between :Fact). Rewritten for the Node layer.

Run from backend/:
  python scripts/verify_relation_e2e.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core import event_bus, neo4j_client  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.schema import apply_schema  # noqa: E402
from app.pipeline.dreaming import run_dreaming_pipeline  # noqa: E402
from app.pipeline.ingestion import run_ingestion_pipeline  # noqa: E402

NARRATIVE = """
Il Vento e il Sole discussero su chi fosse il più forte. Videro un viandante
che camminava lungo la strada avvolto in un mantello. Il Vento soffiò con
tutta la sua forza cercando di strappare il mantello, ma il viandante se lo
strinse ancora di più intorno alle spalle. Allora il Sole uscì da dietro le
nuvole e scaldò la terra: il viandante, sentendosi caldo, si tolse il mantello.
Così il Sole dimostrò di essere più forte del Vento.
""".strip()


async def main() -> int:
    if not settings.OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY missing")
        return 2

    apply_schema()
    await neo4j_client.close_neo4j_driver()
    await neo4j_client.init_neo4j_driver()
    driver = neo4j_client.get_driver()

    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")

    doc_id = f"r25-sole-vento-{uuid.uuid4().hex[:8]}"
    ingest_job = f"ingest-{doc_id}"
    dream_job = f"dream-{doc_id}"
    event_bus.reset_event_bus()

    print(f"Ingesting doc_id={doc_id} ...")
    await run_ingestion_pipeline(doc_id, NARRATIVE, ingest_job)

    async with driver.session() as session:
        nodes_before = await session.run("MATCH (n:Node) RETURN count(n) AS n")
        nb = await nodes_before.single()
        node_count = int(nb["n"]) if nb else 0
        print(f"Nodes after ingest: {node_count}")

    if node_count < 1:
        print("FAIL: need ≥1 :Node from the narrative")
        await neo4j_client.close_neo4j_driver()
        return 1

    print("Running dreaming ...")
    stats = await run_dreaming_pipeline(dream_job, doc_id=doc_id)
    print(f"Dreaming stats: node_drift_count={stats.node_drift_count}")

    async with driver.session() as session:
        rels = await session.run("MATCH ()-[r:Relation]->() RETURN count(r) AS n")
        rec = await rels.single()
        rel_count = int(rec["n"]) if rec else 0

    print(f"Relation edges: {rel_count}")
    await neo4j_client.close_neo4j_driver()

    print("RESULT: OK — narrative produced :Node (and optional :Relation) after ingest+dream")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
