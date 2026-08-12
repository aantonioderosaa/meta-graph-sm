"""R2.5 e2e: ingest narrative → dream → assert horizontal EXTENDS exist.

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
        facts_before = await session.run(
            "MATCH (f:Fact {source_doc_id: $doc_id, is_latest: true}) "
            "RETURN count(f) AS n",
            doc_id=doc_id,
        )
        fb = await facts_before.single()
        fact_count = int(fb["n"]) if fb else 0
        print(f"Facts after ingest (is_latest): {fact_count}")

    if fact_count < 2:
        print("FAIL: need ≥2 facts from the narrative to evaluate horizontal links")
        await neo4j_client.close_neo4j_driver()
        return 1

    print("Running dreaming ...")
    stats = await run_dreaming_pipeline(dream_job, doc_id=doc_id)
    print(
        f"Dreaming stats: edges_created={stats.edges_created} "
        f"facts_processed={stats.facts_processed} groups={stats.groups}"
    )

    async with driver.session() as session:
        extends = await session.run(
            """
            MATCH (a:Fact {source_doc_id: $doc_id})-[r:EXTENDS]->(b:Fact {source_doc_id: $doc_id})
            RETURN count(r) AS n, collect(a.text + ' -> ' + b.text)[0..5] AS samples
            """,
            doc_id=doc_id,
        )
        rec = await extends.single()
        extends_count = int(rec["n"]) if rec else 0
        samples = list(rec["samples"]) if rec else []

        updates = await session.run(
            """
            MATCH (:Fact {source_doc_id: $doc_id})-[r:UPDATES]->(:Fact {source_doc_id: $doc_id})
            RETURN count(r) AS n
            """,
            doc_id=doc_id,
        )
        urec = await updates.single()
        updates_count = int(urec["n"]) if urec else 0

    print(f"EXTENDS within doc: {extends_count}")
    print(f"UPDATES within doc: {updates_count}")
    for sample in samples:
        print(f"  sample: {sample}")

    await neo4j_client.close_neo4j_driver()

    if extends_count >= 1:
        print("RESULT: OK — at least one horizontal EXTENDS among narrative facts")
        return 0

    print("RESULT: FAIL — no EXTENDS between facts of the same narrative document")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
