# Meta-Graph — Grafo di entità, eventi e concetti

Backend FastAPI + Neo4j/GDS + frontend Next.js per ingestione, dreaming e query su un grafo `:Node` / `:Relation` / `:Concept` (chunk condivisi). Il layer `:Fact` è stato rimosso dal backend (piano `piano-implementativo-solo-entita-eventi.md`).

## Documentazione

- Kernel a tre assi (vocabolario chiuso in `backend/app/models/kernel.py`; nessun cambiamento di pipeline in Fase 0)
- Book del dominio / gate genere-vs-filtro e MDL (`backend/app/pipeline/domain_book.py`; `GENRE_NOT_TOPIC_PROMPT` iniettato in estrazione da Fase 3)
- Schema Neo4j esteso additivamente (Fase 2: TBox su `:Concept`, `:ConnectivityRule`, `:CorpusContext`; tipi Famiglia B / backbone). `AUTO_MIGRATE=true` applica tutti gli statement `IF NOT EXISTS` all'avvio; nessun indice/constraint esistente è rimosso.
- Ingestione anti-blur (Fase 3): `:CorpusContext` O(1) per documento, estrazione a due passaggi (entità+summary, poi decisione per coppia), testimoni obbligatori, `kernel_parent` R1–R6, nessun arco per sola co-presenza. `pipeline_complete` resta l'evento SSE finale.
- Backbone/TBox (Fase 4): classificazione `MEMBER_OF` (casa unica) su `:Concept`, match a due livelli (hash/nome esatto → cosine `θ_reuse=0.80` / near-band catch-all), genere nuovo solo se passa il gate genere-vs-filtro e `IS_A` sotto il catch-all kernel. `HAS_CONCEPT` resta il ponte tematico libero. Flag `ENABLE_KERNEL_CLASSIFICATION` (default true).
- PROMOTE (Fase 5): `promote()` sposta un cluster di `:Node` sotto un catch-all kernel o di primo livello in un `:Concept` `promoted=true` (un solo livello; niente cluster di `:Concept`). Atomicità = una transazione `execute_write`; seconda chiamata no-op (id deterministico). Archi esterni sollevati con CREATE (niente fusione); μ congelata su `:TypeMigrationAlias`. Flag `ENABLE_PROMOTE` (default true).
- Fatti all'LCA (Fase 6): un fatto foglia `:Relation` tra `:Node` si scrive una sola volta; la visibilità da un sottodominio è attraversamento (`facts_visible_in_subdomain`, reverse `IS_A`/`MEMBER_OF`), mai una seconda copia. Co-appartenenza ≠ arco. Situazioni condivise → nodo `Evento` + R5 `participates` (`reify_shared_situation`), non un arco «contesto».
- Relazioni S0/S1/S2 (Fase 7): ogni fatto asserito porta `kernel_parent` (altrimenti non si scrive). `PROMOTE` aggiorna il fascio con `update_bundle()` (CREATE, niente fusione). Ogni scrittura asserita deposita una `:ConnectivityRule` (unico canale). `derive_candidate_links` produce ipotesi S2 in memoria con catena e confidenza; **muro S0/S2**: zero `CREATE`/`MERGE` di `:Relation`. `CONNECTIVITY_MAX_GENERALIZATION_HOPS` (default 1) risale `IS_A` fermandosi prima del catch-all kernel.
- Identità per faccette (Fase 8) — **rimossa**: `identity_resolution.py`, `ENABLE_FACET_IDENTITY` e il task giudice che risolveva `POSSIBLY_SAME_AS` sono stati eliminati dalla repo. La deduplica passa sempre da `merge_nodes` (percorso distruttivo, unico rimasto).
- Asse temporale (Fase 9): T1 esteso a tre transizioni (`SUPERSEDES` / `UPDATED_BY` / `CONTRADICTS`); `extends` resta complementare. Un disaccordo non si risolve in silenzio (`CONTRADICTS` tiene entrambe `is_latest=true`). `valid_time` e `system_time` sono proprietà distinte. Flag `ENABLE_TEMPORAL_TRANSITIONS` (default true).
- Giudice (Fase 10): a fine di ogni batch di dreaming (dopo `reconcile`) gira `run_judge` — anti-blur, `EQUIVALENT_TO`, ri-raffinamento storico, smistamento temporale (quattro task; conferma identità e `CONTRADICTS` mancate sono stati rimossi, vedi sotto). Scrive solo primitive esistenti (INGEST/PROMOTE + Famiglia B / `MEMBER_OF`). Ogni passata è loggata in `:JudgeRun`. Flag `ENABLE_JUDGE` (default true); `BACKBONE_COLLAPSE_THRESHOLD=0.90`.
- Riconciliazione incrementale (Fase 18): su ingestioni successive, `merge_nodes` promuove il `summary` più recente sul nodo canonico (lo storico resta sulla catena `merged_into` via `node_history`); dopo la classificazione entity-relation, coppie stesso-head / tail diversi con marcatore temporale esplicito si risolvono nel batch (`SUPERSEDES`/`UPDATED_BY`, mai `DELETE` di nodo/arco). Nessun flag nuovo.
- `CONTRADICTS` mancate (ex Compito 5 del giudice) — **rimosso**: `_task_missed_contradictions` e `ENABLE_MISSED_CONTRADICTIONS` sono stati eliminati (il marcatore lessicale di correzione non si attivava su prosa non contemporanea e produceva `CONTRADICTS` spuri). `CONTRADICTS` continua ad essere scritto da `entity_relation_resolution.py` (coppie stesso-chunk) e da `write_contradicts`; il compito 4 del giudice (smistamento temporale) continua a riclassificarli in `SUPERSEDES`/`UPDATED_BY` quando trova un marcatore.
- Retrieval con metadati (Fase 19): `backend/app/pipeline/context_retrieval.py` espone sei funzioni di sola lettura (`search_fulltext`, `search_vector` sul summary, `get_metadata`, `get_relations`, `get_domain_dictionary`, `facts_from_source`). Ogni `:Node` scritto da `write_node` porta `summary_embedding`; ogni `:Relation` porta `witness_text` cercabile. Nessun flag nuovo. `event_triage.py` (sotto) le chiama in fase 1/2 del suo loop a tre fasi.
- Filtro di rilevanza / ipotesi / quantificatori / ritrattazioni / orchestrazione agentica (Fasi 20–22) — **rimosse**: `relevance_gate.py`, `pending_hypothesis.py`, `quantifier_events.py`, `retraction.py`, `context_agent.py` e `ENABLE_CONTEXT_LAYER` sono stati eliminati dalla repo (nessun rollout di produzione li ha mai attivati). `context_retrieval.py` (Fase 19) resta: è condiviso con `event_triage.py`.
- Query NL coarse-to-fine (Fase 11): `plan_connectivity_scope` interroga `:ConnectivityRule` sulle categorie kernel della domanda **prima** di `hybrid_seed`. `POST /graph/query` aggiunge `citations[]` con `epistemic_status` asserted/derived e `derivation_chain` (passi S0/S1) calcolata in Python, non dall'LLM. I salti S2 restano in memoria e non vengono mai scritti come `:Relation`.
- Layer Metagraph in UI (Fase 12): tab laterali Dominio / Contraddizioni / Regole / Giudice / **Visualizza incompletezze** (liste e albero, nessun canvas NVL extra; il tab Identità è stato rimosso insieme alla Fase 8). Citazioni query ASSERITO/DERIVATO. Incompletezze: `GET /graph/event-incompleteness` (sola lettura, elenco `:EventTriageRun` con `verdict=incomplete`; il flag `ENABLE_EVENT_TRIAGE` non è richiesto per listare).
- Event triage (gated, default **off**): `_task_event_triage` in `run_judge` (ultimo task) gira tre fasi fisse per evento — ricerca (`search_fulltext`/`search_vector` su ogni query proposta), ispezione (`get_relations`/`get_metadata` sui soli id già osservati), decisione (terminale, sempre raggiunta: propone slot o conferma `verified_no_change`). Sostituisce il vecchio loop ReAct aperto (`EventTriageStep`, `EVENT_TRIAGE_MAX_TURNS`) che restava spesso bloccato in "turns exhausted" senza mai arrivare a un verdetto. Turno 0 gratis = `get_relations`/`get_metadata` + testo grezzo `:Chunk` via `DERIVED_FROM` (cap 4000) + prefetch deterministico dei nomi propri nel testo dell'evento. Il giudice non crea mai nuovi `:Node` — assert/retract solo su id già osservati (gate) e già esistenti (MATCH). Scrive solo tramite `validate_slot_proposal` / `apply_validated_slot`. `apply_validated_slot` ritorna False se `head_id`/`tail_id` non esistono come `:Node` o se retract è un no-op. `verified_no_change=True` (lista slot vuota) conferma senza scrittura e senza consumare un check della finestra di attesa; lista vuota senza il flag resta `waiting`/`incomplete`. Audit `:EventTriageRun` keyed per evento. Flag `ENABLE_EVENT_TRIAGE` default **false**. UI: tab **Visualizza incompletezze** legge `GET /graph/event-incompleteness` (nessuna scrittura).
- Vista generale (Fase 15, storica): `GET /graph/macro` resta disponibile (concetti promossi + nodi di primo livello, fasci collassati). **Fase 17 sostituisce l'interazione UI**: dashboard scorrevole di tutti i `:Concept` (`GET /graph/domains`, nessun limit implicito), scheda dizionario/regole (`GET /graph/domains/{id}/dictionary`, `GET /graph/domains/{id}/rules`), grafo a scope annidato (`GET /graph/domains-graph` in radice — solo `:Concept` legati da `BUNDLE`; `GET /graph/domains/{id}/children-graph` al drill). Toggle **Vista dettagliata** (default, quattro pannelli) ↔ **Vista generale** (`DomainDashboard` + `DomainDetailCard` + `DomainGraphPanel` che riusa `GraphPanel`; mai due NVL extra). Freccia Indietro = pop dello stack `drillPath`. Drill-down foglia: `GET /graph/bundle/{a}/{b}` e `GET /graph/metadata/{id}` (`node_type` entity|event).
- Backfill `kernel_category` (Fase 13): job idempotente [`backend/scripts/backfill_kernel_category.py`](./backend/scripts/backfill_kernel_category.py) (`--dry-run`, `--limit`) su `:Node` già ingeriti senza categoria. Non cancella nodi; non richiede dump/restore. `DERIVED_FROM` verso `:Chunk` è già Famiglia B — nessuna riscrittura archi.
- Qualità e accettazione e-e (Fase 14): corpus fisso `tests/test_acceptance_metagraph_e2e.py` (FakeSession, no Docker/OpenAI) + sei stress kernel §13 in `tests/test_kernel_stress.py`; schema Fase 2 in CI Docker (`backend-integration-metagraph`); checklist UI estesa in [`frontend/docs/e12-metagraph-ui-checklist.md`](./frontend/docs/e12-metagraph-ui-checklist.md).
- Checklist UI: [encoding visivo E7](./frontend/docs/e7-visual-encoding-checklist.md), [layer Metagraph E12 / F14.5](./frontend/docs/e12-metagraph-ui-checklist.md).

### Flag Metagraph (default in `Settings`)

Policy di rollout: ogni flag resta spento finché la relativa suite di accettazione non è verde.

| Flag | Default | Perché |
|---|---|---|
| `ENABLE_KERNEL_CLASSIFICATION` | True | Fase 4 suite verde |
| `ENABLE_PROMOTE` | True | Fase 5 suite verde |
| `ENABLE_TEMPORAL_TRANSITIONS` | True | Fase 9 suite verde |
| `ENABLE_JUDGE` | True | Fase 10 suite verde |
| `ENABLE_DERIVES` | False | kill switch preesistente |
| `ENABLE_EVENT_TRIAGE` | **False** | giudice assert/retract su `:Evento`, tre fasi fisse (Macrotask 8–9 verde; spento fino a rollout esplicito) |

`ENABLE_FACET_IDENTITY`, `ENABLE_CONTEXT_LAYER` e `CONTEXT_AGENT_MAX_TURNS` sono stati rimossi insieme al codice che gestivano (Fasi 8 e 20–22, vedi sopra) — non sono più flag validi. `EVENT_TRIAGE_MAX_TURNS` è stato rimosso in un refactor precedente: il loop per-evento è ora tre fasi fisse, non un budget di turni (`EVENT_TRIAGE_MAX_SEARCH_QUERIES` / `EVENT_TRIAGE_MAX_INSPECT_NODES` in `event_triage.py` sono costanti di modulo, non `Settings`).

### Debito noto

`merge_nodes` (percorso distruttivo) e il binario `RelationLabel.replaces` / `extends` restano l'unico percorso di deduplica: la Fase 8 (identità per faccette) è stata rimossa, non solo tenuta spenta. Lo scan periodico sull'intera KB già ingerita resta il residuo #11: la Fase 18 ha solo ristretto la ricerca di contraddizioni mancate al batch corrente, e quel task del giudice è stato poi rimosso del tutto (vedi sopra). Nessun pannello UI di storico (`node_history` è un helper, non un endpoint).

### Stato Fasi Metagraph

| Fase | Nome | Stato |
|------|------|--------|
| 0 | Fondamenta del kernel | completata |
| 1 | Book del dominio | completata |
| 2 | Modello dati Neo4j esteso | completata |
| 3 | Ingestione anti-blur | completata |
| 4 | Backbone/TBox | completata |
| 5 | PROMOTE | completata |
| 6 | Popolamento fatti / LCA | completata |
| 7 | Relazioni S0/S1/S2 | completata |
| 8 | Identità per faccette | **rimossa** (`identity_resolution.py` eliminato; `merge_nodes` unico percorso) |
| 9 | Asse temporale | completata |
| 10 | Il giudice | completata (quattro task; identità e contraddizioni mancate rimossi) |
| 11 | Query engine coarse-to-fine | completata |
| 12 | Frontend — layer Metagraph | completata (tab Identità rimosso) |
| 13 | Migrazione e coesistenza | completata |
| 14 | Qualità e accettazione e-e | completata |
| 15 | Vista a grafo generale | completata (interazione sostituita da Fase 17) |
| 17 | Dashboard sottodomini trasparente | completata |
| 18 | Riconciliazione incrementale su ingestioni successive | completata |
| 19 | Retrieval con metadati | completata (condivisa con `event_triage.py`) |
| 20 | Filtro di rilevanza strutturale / `:PendingHypothesis` | **rimossa** (`relevance_gate.py` / `pending_hypothesis.py` eliminati) |
| 21 | Quantificatori come evento e ritrattazioni globali | **rimossa** (`quantifier_events.py` / `retraction.py` eliminati) |
| 22 | Orchestrazione: il flusso agentico di verifica | **rimossa** (`context_agent.py` eliminato) |

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
3. **Dream** per risoluzione entità/eventi e classificazione relazioni (sempre da `/documents`)
4. Esplora il grafo (si aggiorna da solo a fine pipeline). Interroga con `POST /graph/query` (cronologia `GET /graph/queries`). Gli endpoint Fact (`POST /query`, `GET /graph`, `GET /facts/{id}`, `POST /reconcile`) non esistono più.
5. Per azzerare la knowledge base: su `/documents` → **Elimina tutto** → conferma nel dialog (`DELETE /graph`)

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
  --ignore=tests/test_ingestion_integration.py \
  --ignore=tests/test_embeddings.py --ignore=tests/test_nodes_integration.py \
  --ignore=tests/test_ppr_projection_integration.py \
  --ignore=tests/test_node_query_engine_integration.py \
  --ignore=tests/test_documents_list.py --ignore=tests/test_graph_reset.py

# Rimozione layer Fact — accettazione e-e (no Docker)
cd backend && pytest -q tests/test_acceptance_solo_entita_eventi.py --tb=short

# Layer entità/eventi — accettazione M8 unit (no Docker) e integrazione (Docker)
cd backend && pytest -q tests/test_acceptance_nodes.py --tb=short
cd backend && pytest -q tests/test_nodes_integration.py --tb=short

# Query NL Node/Concept — accettazione Q7 unit (no Docker) e integrazione (Docker)
cd backend && pytest -q tests/test_acceptance_node_query.py --tb=short
cd backend && pytest -q tests/test_node_query_engine_integration.py --tb=short

# Frontend
cd frontend && npm test && npm run lint && npm run build
```

## Convenzione commit

[Conventional Commits](https://www.conventionalcommits.org/): `feat:` / `fix:` / `chore:` / `test:` / `docs:`

## CI

Su ogni `push` / `pull_request` verso `main`:

- **backend** — ruff + unit pytest (i test unitari Metagraph Fasi 0–13 girano su questo job)
- **backend-integration** — suite Neo4j/GDS via Testcontainers (include accettazione §14)
- **frontend** — lint + vitest + build

Workflow: [`.github/workflows/ci.yml`](./.github/workflows/ci.yml)

## Note operative

- **OpenAI costi/rate-limit**: tutte le chiamate passano da `app/core/llm_client.py` (retry, timeout 30s, semaforo `LLM_MAX_CONCURRENCY`) — tech-spec §18. Il modello di default è `OPENAI_MODEL` (`gpt-4o-mini`).
- **Cambio modello embedding**: gli indici vettoriali sono fissati a 768 dim (`EMBEDDING_MODEL` = `BAAI/bge-base-en-v1.5`); un cambio modello richiede ricreare indici e ricalcolare embedding — tech-spec §15.
- **CORS**: `CORS_ORIGINS` (default `http://localhost:3000`) elenca le origini browser autorizzate a chiamare l'API — va estesa (valori separati da virgola) se il frontend gira su un host/porta diversa.
- **Schema Neo4j**: `AUTO_MIGRATE=true` (default) applica constraint/indici all'avvio del backend; in test/CI di solito è `false` e lo schema è applicato esplicitamente.
- **Backfill `kernel_category`**: su una KB già popolata, da `backend/`: `python scripts/backfill_kernel_category.py --dry-run` poi senza `--dry-run`. Idempotente; `--limit` opzionale. Non cancella nulla.
- **GDS 2.12.0 pinnato**: il jar è in `neo4j-plugins/` e montato su `/plugins` (Compose e Testcontainers). Non si usa `NEO4J_PLUGINS` per GDS — quella env var scarica sempre l'ultima versione compatibile da `graphdatascience.ninja`, non un pin. `CALL gds.version()` deve restituire `2.12.0`. Checksum: `neo4j-plugins/SHA256SUMS`.
- **Mock FE**: `NEXT_PUBLIC_USE_MOCK_EVENTS=true` per Pipeline offline. `NEXT_PUBLIC_API_URL` punta al backend (default Compose: `http://localhost:8000`).
- **Layer Fact rimosso**: niente più `:Fact`, `POST /query`, `GET /facts/{id}` né indici `fact_*`. Dreaming pubblica `reconciliation` poi `done`. Ingestione estrae solo nodi (`process_chunk_node_extraction`).

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

### Rimozione layer Fact (piano `piano-implementativo-solo-entita-eventi.md`)

| Macrotask | Contenuto | Stato |
|-----------|-----------|--------|
| M1 Backend: rimozione pipeline Fact | file/API/schema Fact eliminati; `DELETE /graph` in `node_graph`; dreaming solo Node | completata |
| M2 Backend: riferimenti condivisi | `DocumentSummary.node_count`; ingestione solo `node_extraction` | completata |
| M3 Frontend: rimozione Fact-only | GraphExplorer / QueryPanel / GraphSlice | completata |
| M4 Grafo unico + fix WebGL | un solo mount, `onInitializationError` | completata |
| M5 Toggle ponte concetti | `include_concepts` su entità/eventi | completata |
| M6 Naming eventi/Fact | stage pipeline, colonna Nodi, mock SSE | completata |
| M7 Test suite | rimuovi/aggiorna test Fact | completata |
| M8 Accettazione e-e | scenari ingest/dream/dashboard/reset | completata |

### Layer Entità / Eventi / Concetti

Schema `:Node` / `:Concept` / `:Relation` (piano `piano-implementativo-entita-eventi-concetti.md`). Il layer `Fact` non è più nel backend.

| Macrotask | Contenuto | Stato |
|-----------|-----------|--------|
| M1 Schema Neo4j | constraint/indici `:Node`/`:Concept`/`:Relation` (tutti `IF NOT EXISTS`) | completata |
| M2 Estrazione | entità/eventi/concetti da chunk (porting prompt autoschema) | completata |
| M3 Risoluzione entità | dedup incrementale `Node{type:'entity'}` (pattern graphiti) | completata |
| M4 Risoluzione archi | normalizzazione `:Relation` + merge eventi | completata |
| M5 Dreaming esteso | nuovi stage nella pipeline di dreaming esistente | completata |
| M6 API quattro viste | endpoint grafo entità / concetti / eventi / partecipazione | completata |
| M7 Frontend | quattro pannelli sullo stesso piano (dashboard: tab Fatti vs Entità/Eventi) | completata |
| M8 Test e-e / acceptance | criteri complessivi end-to-end | completata |

La dashboard monta un solo `EntityEventExplorer` (quattro pannelli: entità, concetti, eventi, partecipazione). Non esiste più il tab Fatti. Accettazione M8 storica: `pytest tests/test_acceptance_nodes.py` (unit, no Docker) e `pytest tests/test_nodes_integration.py` (Neo4j via testcontainers).

### Query NL sul layer Node / Concept

Endpoint: `POST /graph/query` interroga `:Node` / `:Relation` / `:Concept` con seeding ibrido, Personalized PageRank (GDS) e reranking. `POST /query` (Fact) è stato rimosso.

| Macrotask | Contenuto | Stato |
|-----------|-----------|--------|
| Q1 Schema + pin GDS | embedding/indici su Concept e Relation; fulltext; GDS 2.12.0 jar locale | completata |
| Q2 Proiezione GDS | `nodeQueryGraph` refresh a fine dreaming + lazy ensure | completata |
| Q3 Query engine | seeding ibrido → PPR → cross-encoder → contesto | completata |
| Q4 NodeQueryLog | cronologia `:NodeQueryLog` distinta da `:QueryLog` | completata |
| Q5 API | `POST /graph/query`, `GET /graph/queries[/{id}]` | completata |
| Q6 Frontend | `NodeQueryPanel` sul tab Entità/Eventi | completata |
| Q7 Accettazione e-e | scenari PPR / relazione / isolamento Fact | completata |

`NodeQueryPanel` è l'unico pannello query (`POST /graph/query`).

Accettazione Q7: `pytest tests/test_acceptance_node_query.py` (unit, no Docker) + scenari integrazione in `tests/test_node_query_engine_integration.py`.

## Note Epic 10

`docker compose up` avvia neo4j+backend+frontend con healthcheck. Checklist UI: [encoding visivo E7](./frontend/docs/e7-visual-encoding-checklist.md), [layer Metagraph E12 / F14.5](./frontend/docs/e12-metagraph-ui-checklist.md). CI: `backend` (unit, no Docker), `backend-integration`, `backend-integration-metagraph` (schema Fase 2 + `test_schema.py`).
