# Backend — Event Graph

FastAPI con un solo router attivo: **`/event-graph/*`** (`app.api.event_graph`). Settings in `app.pipeline.event_graph.config` (`EventGraphSettings`, env con prefisso `NEO4J_*`, `OPENAI_*`, `CORS_ORIGINS`, `EVENT_GRAPH_*`).

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Health: `GET /health` e `GET /event-graph/health` (ping Neo4j). OpenAPI: `http://localhost:8000/docs`.

Test unitari event-graph (no Docker):

```bash
pytest -q tests/test_event_graph_*.py tests/test_acceptance_event_graph_e2e.py --ignore=tests/test_event_graph_integration.py
```

Il codice Metagraph legacy (`app/api/documents.py`, `app/pipeline/ingestion.py`, …) resta in tree ma **non** è montato da `app.main`.
