# Meta-Graph — Motore del Grafo dei Fatti (Milestone 1)

Backend FastAPI + Neo4j/GDS + frontend Next.js per ingestione, dreaming e query su un grafo di fatti versionato.

## Documentazione

- [Scope e semantica](./milestone1/milestone1.md)
- [Specifica tecnica](./milestone1/milestone1-tech-spec.md)
- [Piano implementativo (epic/task)](./milestone1/milestone1-implementation-plan.md)
- [Piano fix post-E10 (F1–F4)](./milestone1/milestone1-fixes-plan.md)
- [Piano coerenza documento/chunk e reset KB (R1–R4)](./milestone1/milestone1-relation-detection-plan.md)
- [Piano ragionamento temporale in `replaces` (T1)](./milestone1/milestone1-temporal-reasoning-plan.md)
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

1. Vai su **Documenti** (`/documents`), ingerisci un documento (doc_id + testo → **Ingest**)
2. Osserva il **Pipeline Monitor** sulla dashboard principale (`/`) — resta aggiornato anche se hai lanciato l'azione da un'altra pagina (SSE globale)
3. **Dream** per consolidamento/relazioni (sempre da `/documents`)
4. Esplora il grafo (si aggiorna da solo a fine pipeline); interroga dal **Query Panel**; richiama query precedenti dalla cronologia a tendina
5. Per azzerare la knowledge base: su `/documents` → **Elimina tutto** → conferma nel dialog

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

- **OpenAI costi/rate-limit**: tutte le chiamate passano da `app/core/llm_client.py` (retry, timeout 30s, semaforo `LLM_MAX_CONCURRENCY`) — tech-spec §18. Il modello di default è `OPENAI_MODEL` (`gpt-4o-mini`).
- **Cambio modello embedding**: gli indici vettoriali sono fissati a 768 dim (`EMBEDDING_MODEL` = `BAAI/bge-base-en-v1.5`); un cambio modello richiede ricreare indici e ricalcolare embedding — tech-spec §15.
- **CORS**: `CORS_ORIGINS` (default `http://localhost:3000`) elenca le origini browser autorizzate a chiamare l'API — va estesa (valori separati da virgola) se il frontend gira su un host/porta diversa.
- **Schema Neo4j**: `AUTO_MIGRATE=true` (default) applica constraint/indici all'avvio del backend; in test/CI di solito è `false` e lo schema è applicato esplicitamente.
- **Mock FE**: `NEXT_PUBLIC_USE_MOCK_EVENTS=true` per Pipeline offline; `NEXT_PUBLIC_USE_QUERY_FIXTURE=true` per Query Panel senza backend. `NEXT_PUBLIC_API_URL` punta al backend (default Compose: `http://localhost:8000`).
- **`derives` temporaneamente disattivata**: `ENABLE_DERIVES=false` (default) — la semantica dell'astrazione va rivista (vedi discussione in chat/tech-spec) prima di riabilitarla. Con il flag off, i gruppi di fatti simili non vengono più collassati in un'astrazione: ogni fatto è valutato singolarmente per `updates`/`extends`. `ENABLE_DERIVES=true` riattiva il meccanismo com'era.

## Limiti della knowledge base

### Formati ingeribili

Solo **testo semplice** o **Markdown**, passati come stringa (`doc_id` + `text` a `POST /documents` o dal form in `/documents`). Non c'è parsing di PDF, DOCX, HTML, immagini o altri formati strutturati: va convertito in testo prima dell'ingest. Il Markdown è trattato come testo piano nel chunking (intestazioni e liste non diventano struttura del grafo).

### Cosa il sistema coglie (e cosa no) a livello temporale

Nella classificazione `replaces` / `UPDATES`, il segnale primario sono i **marcatori temporali espliciti nel contenuto** (date, espressioni come «ora», «da allora», «fino al», «il mese scorso»). Senza un segnale del genere, il sistema è istruito a **non** trattare l'ordine di presentazione dei fatti come priorità cronologica.

**Non** è un segnale temporale valido:

- la posizione o l'ordine di lettura nel documento;
- la struttura di una conversazione multi-turno (chi ha detto cosa e in quale ordine) — un transcript viene letto come prosa continua, non come sequenza di turni datati.

Se stai ingerendo un chat log o un diario senza date/espressioni temporali nel testo, aspetati che fatti sequenziali restino affiancati (`extends`) piuttosto che sovrascriversi a vicenda.

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

### Fix post-E10 (piano `milestone1-fixes-plan.md`)

Stato verificato contro il codice (non solo le checkbox del piano).

| Epic | Contenuto | Stato |
|------|-----------|--------|
| F1 Citazioni strutturate | `cited_fact_ids` in `query_engine` / `QueryResponse` + UI citazioni | completata |
| F2 Stabilità Graph Explorer | pulse batched in `store.ts` (`PULSE_DURATION_MS`) | completata |
| F3 Pagina Documenti + refresh | `/documents`, SSE globale, auto-refresh grafo a fine pipeline | completata |
| F4 Cronologia query | `QueryLog` + tendina in Query Panel | completata |

### Relation-detection / reset KB (piano `milestone1-relation-detection-plan.md`)

| Epic | Contenuto | Stato |
|------|-----------|--------|
| R1 Candidati chunk/doc | `find_candidates` con fonti embedding/chunk/doc + dedup coppie | completata |
| R2 Classificazione `extends` | segnale località nel prompt + system prompt allargato | completata |
| R3 Reset knowledge base | `DELETE /graph` + UI «Elimina tutto» con conferma e clear client | completata |
| R4 Igiene README / gitignore | link piani, ciclo UI, env vars, tabelle progresso; `.loop-progress.md` ignorato | completata |

### Ragionamento temporale in `replaces` (piano `milestone1-temporal-reasoning-plan.md`)

| Epic | Contenuto | Stato |
|------|-----------|--------|
| T1 Marcatori temporali + limiti KB | prompt `replaces` guidato da marker nel testo + prudenza senza segnale; sezione README limiti | completata |

### Layer Entità / Eventi / Concetti

Schema `:Node` / `:Concept` / `:Relation` accanto al layer `Fact`/`Chunk` esistente (piano `piano-implementativo-entita-eventi-concetti.md`).

| Macrotask | Contenuto | Stato |
|-----------|-----------|--------|
| M1 Schema Neo4j | constraint/indici `:Node`/`:Concept`/`:Relation` (tutti `IF NOT EXISTS`) | completata |
| M2 Estrazione | entità/eventi/concetti da chunk (porting prompt autoschema) | completata |
| M3 Risoluzione entità | dedup incrementale `Node{type:'entity'}` (pattern graphiti) | completata |
| M4 Risoluzione archi | normalizzazione `:Relation` + merge eventi | completata |
| M5 Dreaming esteso | nuovi stage nella pipeline di dreaming esistente | in attesa |
| M6 API quattro viste | endpoint grafo entità / concetti / eventi / partecipazione | in attesa |
| M7 Frontend | quattro pannelli sullo stesso piano | in attesa |
| M8 Test e-e / acceptance | criteri complessivi end-to-end | in attesa |

## Note Epic 10

Suite accettazione `tests/test_acceptance_milestone1.py` (8/8 criteri §8). `docker compose up` avvia neo4j+backend+frontend con healthcheck. Checklist UI: `milestone1/e10-manual-ui-checklist.md`. CI con job `backend-integration` come gate.
