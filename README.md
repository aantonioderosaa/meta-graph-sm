# Meta-Graph — Motore del Grafo dei Fatti (Milestone 1)

Backend FastAPI + Neo4j/GDS + frontend Next.js per ingestione, dreaming e query su un grafo di fatti versionato.

## Documentazione

- [Scope e semantica](./milestone1/milestone1.md)
- [Specifica tecnica](./milestone1/milestone1-tech-spec.md)
- [Piano implementativo (epic/task)](./milestone1/milestone1-implementation-plan.md)
- [Checklist UI manuale §8](./milestone1/e10-manual-ui-checklist.md)

## Prerequisiti

- Docker Desktop (stack completo o solo Neo4j)
- Python **3.12+** / Node.js **22+** (solo se non usi Compose per backend/frontend)
- Chiave OpenAI in `.env` (estrazione/dreaming/query reali)

## Avvio consigliato — tutto lo stack

```bash
cp .env.example .env
# Imposta OPENAI_API_KEY e NEO4J_PASSWORD

docker compose up --build
# Neo4j Browser  http://localhost:7474
# Backend API    http://localhost:8000/docs
# Frontend UI    http://localhost:3000
```

Ordine di avvio: Neo4j (healthy) → backend (AUTO_MIGRATE + health) → frontend.

Ciclo tipico in UI (con `NEXT_PUBLIC_USE_MOCK_EVENTS=false`):

1. Ingest documento dalla barra eventi (doc_id + testo → **Ingest**)
2. Osserva Pipeline Monitor (SSE)
3. **Dream** per consolidamento/relazioni
4. Esplora il grafo; interroga dal Query Panel

## Avvio locale (dev, meno di 15 min)

```bash
cp .env.example .env
docker compose up -d neo4j

cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000

cd ../frontend
cp .env.local.example .env.local
# Per SSE reale: NEXT_PUBLIC_USE_MOCK_EVENTS=false
npm ci --legacy-peer-deps
npm run dev
```

## Test

```bash
# Backend — unit (veloci, no Docker)
cd backend && pytest -q --ignore=tests/test_schema.py --ignore=tests/test_health.py \
  --ignore=tests/test_ingestion_integration.py --ignore=tests/test_dreaming_integration.py \
  --ignore=tests/test_query_integration.py --ignore=tests/test_acceptance_milestone1.py \
  --ignore=tests/test_embeddings.py

# Backend — accettazione §8 + integrazione (richiede Docker)
cd backend && pytest -q tests/test_acceptance_milestone1.py --tb=short

# Frontend
cd frontend && npm test && npm run lint && npm run build
```

## Convenzione commit

[Conventional Commits](https://www.conventionalcommits.org/): `feat:` / `fix:` / `chore:` / `test:` / `docs:`

## CI

Su ogni `push` / `pull_request` verso `main`:

- **backend** — ruff + unit pytest
- **backend-integration** — suite Neo4j/GDS via Testcontainers (include accettazione §14)
- **frontend** — lint + vitest + build

Workflow: [`.github/workflows/ci.yml`](./.github/workflows/ci.yml)

## Note operative

- **OpenAI costi/rate-limit**: tutte le chiamate passano da `app/core/llm_client.py` (retry, timeout 30s, semaforo `LLM_MAX_CONCURRENCY`) — tech-spec §18.
- **Cambio modello embedding**: gli indici vettoriali sono fissati a 768 dim (`BAAI/bge-base-en-v1.5`); un cambio modello richiede ricreare indici e ricalcolare embedding — tech-spec §15.
- **Mock FE**: `NEXT_PUBLIC_USE_MOCK_EVENTS=true` per Pipeline offline; `NEXT_PUBLIC_USE_QUERY_FIXTURE=true` per Query Panel senza backend.

## Progresso Milestone 1

| Epic | Stato |
|------|--------|
| E0 Fondamenta progetto | completata |
| E1 Schema dati Neo4j | completata |
| E2 Backend skeleton + contratti | completata |
| E3 Ingestione | completata |
| E4 Dreaming | completata |
| E5 Query engine | completata |
| E6 Frontend scaffold | completata |
| E7 Graph Explorer | completata |
| E8 Pipeline Monitor | completata |
| E9 Query Panel | completata |
| E10 Qualità e accettazione | completata |

## Note Epic 10

Suite accettazione `tests/test_acceptance_milestone1.py` (8/8 criteri §8). `docker compose up` avvia neo4j+backend+frontend con healthcheck. Checklist UI: `milestone1/e10-manual-ui-checklist.md`. CI con job `backend-integration` come gate.
