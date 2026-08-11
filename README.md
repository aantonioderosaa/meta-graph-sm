# Meta-Graph — Motore del Grafo dei Fatti (Milestone 1)

Backend FastAPI + Neo4j/GDS + frontend Next.js per ingestione, dreaming e query su un grafo di fatti versionato.

## Documentazione

- [Scope e semantica](./milestone1/milestone1.md)
- [Specifica tecnica](./milestone1/milestone1-tech-spec.md)
- [Piano implementativo (epic/task)](./milestone1/milestone1-implementation-plan.md)

## Prerequisiti

- Docker Desktop (Neo4j + GDS)
- Python **3.12+** (testato anche con 3.13)
- Node.js **22+** / npm
- (da Epic 3+) chiave OpenAI in `.env`

## Avvio locale (meno di 15 min)

```bash
# 1. Env
cp .env.example .env

# 2. Neo4j + Graph Data Science
docker compose up -d neo4j
# Browser: http://localhost:7474  |  Bolt: bolt://localhost:7687
# Credenziali: neo4j / valore di NEO4J_PASSWORD (.env)
# Verifica GDS: CALL gds.version();

# 3. Backend
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
# Health: http://localhost:8000/health  →  {"status":"not_implemented"}

# 4. Frontend (altra shell)
cd frontend
cp .env.local.example .env.local   # opzionale
npm ci --legacy-peer-deps
npm run dev
# UI: http://localhost:3000
```

Lo schema Neo4j (constraint, indici, vector index 768/cosine) viene applicato automaticamente all'avvio del backend se `AUTO_MIGRATE=true` (default). In alternativa:

```bash
cd backend
python scripts/init_db.py
```

## Convenzione commit

Usare [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` nuova funzionalità
- `fix:` bugfix
- `chore:` tooling / infra / deps
- `test:` test
- `docs:` documentazione

Esempio: `chore(e0): scaffold repo, CI e Neo4j compose`

## CI

Su ogni `push` / `pull_request` verso `main`, GitHub Actions esegue in parallelo:

- **backend**: `ruff check` + `pytest` (i test di schema usano Testcontainers — Docker richiesto sul runner)
- **frontend**: `npm ci --legacy-peer-deps` + `npm run lint` + `npm run build`

Workflow: [`.github/workflows/ci.yml`](./.github/workflows/ci.yml)

## Struttura repo

```
backend/          FastAPI (app/, tests/, scripts/init_db.py)
frontend/         Next.js App Router + shadcn/ui + Zustand + NVL
milestone1/       requisiti e piano
docker-compose.yml   (Epic 0: solo Neo4j; backend/frontend in E10)
```

## Progresso Milestone 1

| Epic | Stato |
|------|--------|
| E0 Fondamenta progetto | completata |
| E1 Schema dati Neo4j | completata |
| E2 Backend skeleton + contratti | completata |
| E3 Ingestione | completata |
| E4 Dreaming | pending |
| E5 Query engine | pending |
| E6 Frontend scaffold | pending |
| E7 Graph Explorer | pending |
| E8 Pipeline Monitor | pending |
| E9 Query Panel | pending |
| E10 Qualità e accettazione | pending |

## Note Epic 0

Nessuna logica applicativa (modelli dominio, endpoint di business, componenti React di prodotto): solo infrastruttura condivisa per le epic successive.

## Note Epic 1

Schema Cypher versionato in `backend/app/db/schema.cypher` (tech-spec §4.2), bootstrap idempotente via `scripts/init_db.py` e hook `AUTO_MIGRATE` all'avvio FastAPI. Vector index su `Fact.embedding` e `Chunk.embedding`: 768 dimensioni, similarità cosine.

## Note Epic 2

Contratti API/eventi congelati (SYNC POINT 1): modelli Pydantic §17 in `backend/app/models/`, endpoint REST stub in `backend/app/api/`, SSE su `/events/stream`, wrapper LLM unico in `app/core/llm_client.py`. `GET /health` verifica Neo4j + GDS. OpenAPI: `http://localhost:8000/docs`.

## Note Epic 3

`POST /documents` esegue la pipeline reale: chunking → embedding locale (`BAAI/bge-base-en-v1.5`, 768 dim) → scrittura `Chunk` → estrazione LLM via wrapper → `Fact`+`DERIVED_FROM` (rumore scartato). Eventi SSE fino a `pipeline_complete`. Richiede `OPENAI_API_KEY` in `.env` per estrazione reale; i test mockano l'LLM. Primo caricamento del modello embedding: diversi minuti a freddo (download), poi singleton in memoria.
