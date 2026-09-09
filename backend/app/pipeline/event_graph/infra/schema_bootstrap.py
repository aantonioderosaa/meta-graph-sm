"""Apply the event-graph Cypher schema with the package AsyncDriver."""

from __future__ import annotations

from pathlib import Path

from neo4j import AsyncDriver

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.cypher"

_schema_applied = False


def load_schema_statements(path: Path = SCHEMA_PATH) -> list[str]:
    """Split schema.cypher into individual executable statements."""
    raw = path.read_text(encoding="utf-8")
    statements: list[str] = []
    buffer: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buffer).rstrip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
            buffer = []
    if buffer:
        stmt = "\n".join(buffer).strip()
        if stmt:
            statements.append(stmt)
    return statements


def reset_schema_gate() -> None:
    """Clear the in-process apply gate (for tests)."""
    global _schema_applied
    _schema_applied = False


async def ensure_event_graph_schema(driver: AsyncDriver) -> int:
    """Load schema.cypher, split on ``;``, execute each statement.

    In-process gate: skip re-apply after the first successful run in this
    process. Statements themselves use ``IF NOT EXISTS``.
    """
    global _schema_applied
    if _schema_applied:
        return 0
    statements = load_schema_statements()
    async with driver.session() as session:
        for statement in statements:
            await session.run(statement)
    _schema_applied = True
    return len(statements)
