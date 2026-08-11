"""FastAPI application entrypoint — Milestone 1 skeleton (Epic 0)."""

from fastapi import FastAPI

app = FastAPI(title="Meta-Graph Facts Engine", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    """Placeholder health check — replaced with Neo4j+GDS probe in Epic 2."""
    return {"status": "not_implemented"}
