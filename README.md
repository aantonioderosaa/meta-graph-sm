# Meta-Graph — Grafo degli eventi (Event Graph)

Prodotto **live** in questo branch: backend FastAPI che espone solo **`/event-graph/*`**, frontend Next.js con **`EventGraphShell`** sulla home (`/`). Ingestione testo → pipeline MACRO/MICRO → Neo4j → SSE → refresh del grafo in UI.

> **Codice Metagraph dormiente:** kernel, giudice, dreaming, `POST /documents`, `POST /graph/query`, `DomainDashboard` / `AppShell` restano nel repository ma **non** sono montati (`app/main.py` include solo `event_graph`). Non usarli come riferimento operativo.

Piani storici Metagraph: file `PIANO-*.md` in root (non aggiornati allo stato del prodotto live).

## Prerequisiti

- Docker Desktop (stack completo o solo Neo4j)
- Python **3.12+** / Node.js **22+** (se non usi Compose per backend/frontend)
- Chiave OpenAI (o endpoint compatible) per estrazione LLM reale

## Avvio — Docker Compose

```bash
cp .env.example .env
# Imposta OPENAI_API_KEY e, se vuoi, NEO4J_PASSWORD

docker compose up --build
# Neo4j Browser  http://localhost:7474
# Backend API    http://localhost:8000/docs
# Frontend UI    http://localhost:3000
```

Ordine: Neo4j (healthy) → backend (`GET /health` o `/event-graph/health`) → frontend.

Il servizio Neo4j monta ancora il plugin GDS da `neo4j-plugins/`; **il percorso event-graph live non usa GDS** (solo driver Neo4j + Cypher).

## Avvio locale (dev)

```bash
cp .env.example .env
docker compose up -d neo4j

cd backend && pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000

cd ../frontend
cp .env.local.example .env.local
npm ci --legacy-peer-deps && npm run dev
```

## Ciclo in UI

Con `NEXT_PUBLIC_USE_MOCK_EVENTS=false` (default in `.env.local.example`):

1. **`EventIngestPanel`**: `doc_id` + testo → **Ingerisci** → `POST /event-graph/documents` → risposta `{ job_id }`.
2. **`EventPipelineMonitor`**: `EventSource` su `GET /event-graph/stream?job_id=…` (SSE).
3. A `stage: done` / evento `pipeline_complete`, **`EventGraphShell`** richiama `loadGraph()` → `GET /event-graph/graph` (e catalog/stats).
4. Scegli la **vista** (Tutto / Ordine / Temporale / Relazioni). **Temporale** è il livello ancore (`AncoraTemporale`, acceso di default con `EVENT_GRAPH_TEMPORAL_ENABLED=true`).

## Percorso ingest (tecnico)

1. Frontend `ingestDocument` → `POST /event-graph/documents` con `{ doc_id, text }`.
2. API crea `job_id`, avvia `asyncio.create_task(run_tracked_job(job_id, run_event_graph_ingestion(...)))`.
3. `run_event_graph_ingestion`: schema Neo4j event-graph se necessario.
4. **MACRO:** `segmenta_zone` → `riassumi_zone` (LLM) → `collega_zone` → `calcola_vie` / `annota_vie` → persist documento/zone/archi macro → SSE `macro_done`.
5. **MICRO** (per zona, tutte o solo vie principali se `EVENT_GRAPH_FLASH_MODE`): `espandi_zona` → `espandi_zona_fino_dedup` / `extract_event_entities` → coref menzioni → `collega_inter_frase` → `chiusura_temporale` (Allen) → persist zona + sotto-grafo.
6. **Dorsale:** `collega_dorsale_eventi` (SEQUENZA intra-zona) + `collega_dorsale_zone` (ponti inter-zona).
7. **`genera_transizioni_zona`** → persist `SUCCESSIONE_ZONA` (se LLM ok).
8. **Livello ancore (documento):** `esegui_livello_ancore` (estrazione → identità → linea → smistamento → relazioni → persist). Con `EVENT_GRAPH_TEMPORAL_ENABLED=false` il passo non gira.
9. **Livello relazioni (documento):** `estrai_livello_relazioni` + persist.
10. **Fase B:** coref eventi, persist, SSE `reconcile_done`. `temporal_placement.esegui` non è più sul percorso live.
11. SSE finale `pipeline_complete` con statistiche; il client chiude lo stream e refresha il grafo.

## API principali

| Metodo | Path | Uso |
|--------|------|-----|
| POST | `/event-graph/documents` | Avvia ingest (async) |
| GET | `/event-graph/stream?job_id=` | SSE pipeline |
| GET | `/event-graph/graph?vista=tutto\|ordine\|temporale\|relazioni` | Payload Cytoscape |
| GET | `/event-graph/health` | Ping Neo4j |
| GET | `/health` | Stesso ping (Compose / client legacy) |

Query strutturata/NL: `/event-graph/query/*` (sul grafo eventi, non sul Metagraph).

## Variabili d'ambiente (backend live)

Lette da `EventGraphSettings` (`backend/app/pipeline/event_graph/config.py`):

| Variabile | Default | Nota |
|-----------|---------|------|
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | `bolt://localhost:7687`, … | Driver isolato event-graph |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` | —, vuoto, `gpt-4o-mini` | LLM strutturato |
| `CORS_ORIGINS` | `http://localhost:3000` | |
| `EVENT_GRAPH_FLASH_MODE` | `false` | Solo zone sulle vie principali |
| `EVENT_GRAPH_TEMPORAL_ENABLED` | **`true`** | Livello ancore (`AncoraTemporale`); `false` spegne LLM temporale e scritture |
| `EVENT_GRAPH_LLM_*`, `EVENT_GRAPH_*_CHUNK_WORDS` | vedi `.env.example` | Timeout, concorrenza, chunking |

Frontend: `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_USE_MOCK_EVENTS`.

## Esempio: sole-e-vento

Corpus di riferimento: favola «Il Sole e il Vento» (~340 parole). Payload di esempio in `backend/_ingest_sole_vento.json` (usare **`doc_id`: `sole-e-vento`** come negli script E2E, non `sole-vento`).

Asserzioni codificate (senza dipendere da una corsa Neo4j locale):

| Fonte | Cosa verifica |
|-------|----------------|
| `PIANO-TRE-LIVELLI-GRAFO.md` (A-e1) | **5** nodi `:Zona`, **4** archi `SUCCESSIONE_ZONA` (uno per confine tra zone) |
| `backend/_e2e_assert.py` (E1–E16) | 28–42 `:Evento` attivi (`fuso_in` nullo); 0 eventi `non_finito`; coref menzioni (1 sole, 1 vento, 1 mantello); 0 archi catena `STESSO_EVENTO`/`AGGIORNA`/`CONTRADDICE`; grafo HTTP senza archi catena; catena intrinseca su soffiare+vento |
| `backend/_mt10_measure.json` | Snapshot live storico (ClusterTemporale): 5 zone, 4 `SUCCESSIONE_ZONA`, 6 `ClusterTemporale` — **non** rappresenta il default attuale con ancore |

Script utili (Neo4j + backend in ascolto): `_e2e_assert.py`, `_dump_sole_vento.py`, `tests/test_acceptance_event_graph_e2e.py` (unit, LLM stub, no Docker).

## Limiti

- **Formati:** solo testo/Markdown incollato nel form; niente PDF/DOCX/HTML.
- **Cronologia cross-documento:** rinunciata (ancore locali al documento). `temporal_placement` resta in tree, non chiamato.
- **Metagraph:** non ingeribile dall'UI live; resta codice legacy.

## Test

```bash
# Backend — suite event-graph (no Docker)
cd backend && pytest -q tests/test_event_graph_*.py tests/test_acceptance_event_graph_e2e.py \
  --ignore=tests/test_event_graph_integration.py

# Integrazione Docker (opt-in)
cd backend && pytest -q tests/test_event_graph_integration.py

# Frontend
cd frontend && npm test && npm run lint && npm run build
```

## CI

Su `push` / `pull_request` verso `main` (`.github/workflows/ci.yml`):

- **backend** — ruff + `pytest tests/test_event_graph_*.py` (+ accettazione event-graph, escluso `test_event_graph_integration.py`)
- **backend-integration** / **backend-integration-metagraph** — `if: false` (Metagraph/Docker legacy, solo manuale)
- **frontend** — lint + vitest + build

## Storico — Metagraph (non montato)

Il README precedente descriveva il kernel a tre assi, dreaming, giudice, `POST /graph/query`, dashboard domini e le Fasi 0–22. Quel percorso resta nel codice sotto `app/api/documents.py`, `app/pipeline/ingestion.py`, componenti `DomainDashboard` / `GraphPanel`, ecc., ma **non** è il prodotto avviato da `docker compose up` oggi.

Per dettaglio architetturico storico: tabelle e flag nel commit history / `PIANO-*.md`, checklist UI in `frontend/docs/e7-visual-encoding-checklist.md` e `e12-metagraph-ui-checklist.md`.
