# Specifica Tecnica — Motore del Grafo dei Fatti su Neo4j (Milestone 1)

> Deriva da [`milestone1.md`](./milestone1.md), che resta la fonte di verità per **scope, semantica e criteri di accettazione**. Questo documento non ne modifica il contenuto funzionale: lo **traduce** su uno stack basato su **Neo4j** (invece di Postgres+pgvector) con un **frontend interattivo** per visualizzare grafo, pipeline e query. Dove non specificato altrimenti, valgono le regole semantiche di `milestone1.md` (§3, §4, §5).

---

## 0. Decisioni tecniche e motivazioni

| Decisione | Scelta | Perché |
|---|---|---|
| Backend / pipeline | **Python 3.12 + FastAPI** | È lo stack più efficiente per il carico di lavoro reale del milestone: embedding locale (`sentence-transformers`, PyTorch/ONNX nativi, GPU opzionale) e orchestrazione di chiamate LLM+Neo4j con librerie mature (`neo4j` driver ufficiale, `openai`). Non essendoci vincoli di riuso con supermemory, non ha senso portarsi dietro Node solo per "un linguaggio unico": l'ingestione/dreaming è CPU/ML-bound, dominio in cui Python è più veloce da scrivere e più efficiente da eseguire. |
| Frontend | **TypeScript + Next.js (App Router) + React** | Obbligato dal requisito "buon frontend interattivo"; Next.js dà routing, SSR per il caricamento iniziale del grafo, e un buon ecosistema di componenti (shadcn/ui + Tailwind) per una UI coerente. |
| Grafo/DB | **Neo4j 5.19+ (Community) + plugin Graph Data Science (GDS)** | Vedi §1. GDS sostituisce con algoritmi nativi (kNN, WCC) la logica di clustering che nello stack Postgres andrebbe scritta a mano in Python. |
| LLM | **OpenAI** (`gpt-4o-mini` di default, configurabile) | Scelto dall'utente. Usato per: estrazione fatti (§2.2), consolidamento (§3.2), classificazione relazioni (§3.3), risposta NL in query (§6). Sempre con **structured output** (JSON Schema / function calling), mai free-text parsing. |
| Embedding | **Locale, `BAAI/bge-base-en-v1.5`** (768 dim, cosine) via `sentence-transformers` | Come indicato in `milestone1.md`; nessun costo per-token, nessuna latenza di rete per un'operazione che gira su ogni chunk/fatto. |
| Ampiezza frontend | **Graph Explorer + Pipeline Monitor live + pannello Query NL** | Scelto dall'utente. Vedi §10. |
| Real-time | **SSE (Server-Sent Events)**, non WebSocket | Il flusso di eventi pipeline→UI è unidirezionale (il backend notifica, il client non deve rispondere in-band); SSE è più semplice da implementare/debuggare di un WS e FastAPI lo supporta nativamente con `StreamingResponse`. |
| Auth / multi-tenant | **Fuori scope** (come da `milestone1.md`) | App standalone locale, single-user, nessuna autenticazione. Documentato come assunzione in §15. |

---

## 1. Perché Neo4j cambia (in meglio) l'architettura del milestone

Il modello relazionale di `milestone1.md` (§1) è già "a forma di grafo" travestito da tabelle: `fact_edges` è una tabella di archi, `fact_provenance` una M:N. Su Neo4j questi diventano **relazioni native**, eliminando i join espliciti e rendendo i traversal (§5, §6) query dichiarative a un rigo invece di CTE ricorsive.

Il vantaggio più concreto riguarda però il **dreaming (§3.1)**: nello stack Postgres, "kNN con soglia coseno ~0.80, oppure connected-components su un grafo di similarità" va implementato a mano (query pgvector + libreria grafo in Python, es. `networkx`). Su Neo4j questo è esattamente ciò che fa **Neo4j Graph Data Science (GDS)**:

- `gds.knn` — costruisce un grafo di similarità k-NN sugli embedding dei fatti freschi, con soglia di similarità configurabile (equivalente alla soglia coseno ~0.80).
- `gds.wcc` (Weakly Connected Components) — raggruppa il grafo di similarità in componenti connesse = i "gruppi di fatti correlati" di §3.1.

Questo sposta il clustering **dentro** il database, evita di spostare vettori avanti e indietro verso il processo Python, ed è la ragione tecnica principale per cui Neo4j è una scelta migliore di Postgres per *questo specifico* algoritmo di dreaming, non solo un cambio di database "per requisito".

---

## 2. Architettura d'insieme

```mermaid
flowchart LR
    subgraph Frontend["Frontend — Next.js (TS)"]
        GE[Graph Explorer\n(NVL)]
        PM[Pipeline Monitor\n(SSE log/animazione)]
        QP[Query Panel\n(NL + subgraph highlight)]
    end

    subgraph Backend["Backend — FastAPI (Python)"]
        API[REST API]
        SSE[SSE event bus]
        ING[Ingestion service\nchunking + estrazione]
        DRM[Dreaming service\ngrouping + consolidamento + relazioni]
        QRY[Query service]
        EMB[Embedding\nbge-base-en-v1.5 locale]
    end

    subgraph DB["Neo4j 5.19+ Community + GDS"]
        GRAPH[(Fact / Chunk graph\n+ vector index)]
        GDSLIB[GDS: kNN, WCC]
    end

    OAI[[OpenAI API]]

    GE <-->|REST| API
    QP <-->|REST| API
    PM <-->|SSE stream| SSE

    API --> ING
    API --> DRM
    API --> QRY

    ING --> EMB
    ING -->|estrazione fatti| OAI
    ING --> GRAPH
    ING -.eventi.-> SSE

    DRM --> GDSLIB
    DRM -->|consolidamento + classificazione| OAI
    DRM --> GRAPH
    DRM -.eventi.-> SSE

    QRY --> EMB
    QRY -->|formulazione risposta| OAI
    QRY --> GRAPH
```

**Componenti:**
- **Ingestion service**: §4 — chunking, embedding locale, estrazione fatti via LLM, scrittura su Neo4j.
- **Dreaming service**: §5 — grouping via GDS, consolidamento via LLM, rilevazione relazioni via vector index + LLM, mantenimento `is_latest`.
- **Query service**: §7 — query corrente, storica, NL con provenienza.
- **Event bus**: pub/sub in-process (una coda `asyncio.Queue` per client SSE connesso) che inoltra gli eventi emessi da ingestion/dreaming al Pipeline Monitor. Per il milestone (single-worker, single-user) non serve Redis; è documentato come punto di estensione futura (§15).

---

## 3. Stack tecnologico

| Livello | Tecnologia | Note |
|---|---|---|
| Grafo | Neo4j 5.19+ Community Edition | Vector index nativo (5.13+) + plugin GDS 2.x |
| Backend | Python 3.12, FastAPI, Uvicorn | async end-to-end |
| Driver DB | `neo4j` (driver Python ufficiale) | driver async (`neo4j.AsyncGraphDatabase`) |
| Embedding | `sentence-transformers`, modello `BAAI/bge-base-en-v1.5` | 768 dim, cosine; CPU ok, GPU opzionale via CUDA |
| LLM | `openai` SDK, `gpt-4o-mini` (configurabile via env) | structured output (`response_format={"type":"json_schema", ...}`) |
| Validazione I/O | `pydantic` v2 | schemi per fatti estratti, classificazioni relazione, risposte query |
| Chunking | `langchain-text-splitters` (RecursiveCharacterTextSplitter) o implementazione propria minimale | vedi §4.1 |
| Frontend framework | Next.js 14+ (App Router), React 18, TypeScript | |
| UI kit | Tailwind CSS + shadcn/ui | coerenza visiva, componenti accessibili |
| Visualizzazione grafo | `@neo4j-nvl/react` (Neo4j Visualization Library) | libreria ufficiale Neo4j, stile nativo coerente col dominio, gestisce bene nodi/relazioni tipizzati e styling per-property |
| Streaming realtime | SSE nativo (FastAPI `StreamingResponse` + `EventSource` lato client) | |
| Test | `pytest` + `pytest-asyncio` + `testcontainers[neo4j]` | Neo4j+GDS in container effimero per i test di integrazione |
| Orchestrazione locale | Docker Compose | vedi §11 |

---

## 4. Modello dati in Neo4j

### 4.1 Mapping dallo schema relazionale

| `milestone1.md` (Postgres) | Neo4j |
|---|---|
| tabella `chunks` | label `(:Chunk {id, doc_id, text, embedding, created_at})` |
| tabella `facts` | label `(:Fact {id, text, type, is_latest, confidence, source_doc_id, embedding, created_at})` |
| tabella `fact_provenance` (M:N) | relazione `(:Fact)-[:DERIVED_FROM]->(:Chunk)` |
| tabella `fact_edges` con `type` | tre relazioni tipizzate: `(:Fact)-[:UPDATES]->(:Fact)`, `(:Fact)-[:EXTENDS]->(:Fact)`, `(:Fact)-[:DERIVES]->(:Fact)` |
| indice `facts(is_latest)` | indice nativo su property |
| indice vettoriale su `facts.embedding` | **vector index** nativo Neo4j |
| indice `fact_edges(tgt_fact_id, type)` | implicito: attraversare una relazione tipizzata è già indicizzato nel motore nativo |

Convenzione di direzione (**invariata da `milestone1.md` §1**): `src` = fatto nuovo/derivato, `tgt` = precedente/sorgente. In Cypher: `(nuovo)-[:UPDATES]->(vecchio)`, `(nuovo)-[:EXTENDS]->(esistente)`, `(derivato)-[:DERIVES]->(sorgente)`.

### 4.2 Schema Cypher (constraints + indici)

```cypher
// Identità
CREATE CONSTRAINT fact_id IF NOT EXISTS FOR (f:Fact) REQUIRE f.id IS UNIQUE;
CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE;

// Filtri frequenti
CREATE INDEX fact_is_latest IF NOT EXISTS FOR (f:Fact) ON (f.is_latest);
CREATE INDEX fact_type       IF NOT EXISTS FOR (f:Fact) ON (f.type);
CREATE INDEX fact_doc        IF NOT EXISTS FOR (f:Fact) ON (f.source_doc_id);
CREATE INDEX chunk_doc       IF NOT EXISTS FOR (c:Chunk) ON (c.doc_id);

// Vector index — 768 dim (bge-base-en-v1.5), similarità coseno
CREATE VECTOR INDEX fact_embedding IF NOT EXISTS
FOR (f:Fact) ON (f.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};

CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
FOR (c:Chunk) ON (c.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};
```

`confidence` (default `1.0`) e la colonna riservata `forget_after` (nullable, non usata) restano come da §1 di `milestone1.md`: solo proprietà memorizzate, nessuna logica applicata in questo milestone.

---

## 5. Pipeline di ingestione (§2 di `milestone1.md`)

Endpoint: `POST /documents` → `{doc_id, text}` (o upload file testuale). Risposta immediata `{job_id}`; il lavoro procede in background emettendo eventi SSE su `/events/stream?job_id=...`.

**5.1 Chunking.** Splitter ricorsivo ~256–512 token, overlap 10–15% (per-frase se il documento è corto/strutturato). Nessuna chiamata LLM. Per ciascun chunk:
1. calcola embedding locale (bge-base-en-v1.5);
2. `MERGE (c:Chunk {id: $id}) SET c.doc_id=$doc_id, c.text=$text, c.embedding=$emb, c.created_at=datetime()`;
3. emetti evento `chunk_created`.

**5.2 Estrazione fatti + filtro rumore.** Una chiamata OpenAI per chunk (o piccolo gruppo contiguo), **structured output** vincolato allo schema:

```json
{ "facts": [ { "text": "string", "type": "fact|preference|episode" } ] }
```

Se `facts` è vuoto → chunk scartato come rumore, evento `chunk_discarded_noise`. Altrimenti per ogni fatto:

```cypher
CREATE (f:Fact {
  id: $fid, text: $text, type: $type,
  is_latest: true, confidence: 1.0,
  source_doc_id: $doc_id, embedding: $emb,
  created_at: datetime()
})
WITH f
MATCH (c:Chunk {id: $chunk_id})
CREATE (f)-[:DERIVED_FROM]->(c)
```

Evento `fact_extracted`. A fine ingestione i fatti sono "grezzi": nessuna relazione, `is_latest` non ancora riconciliato — esattamente come da `milestone1.md` §2. Se ne occupa il dreaming.

**5.3 Prompt di estrazione (structured output).** Schema e contratto dati completi in §17; qui il template esatto.

- **System prompt:**
  > Sei un estrattore di fatti atomici da testo. Estrai solo affermazioni autosufficienti (comprensibili senza il contesto del chunk), verificabili o comunque dichiarative. Ignora saluti, conferme vuote ("ok", "capito"), domande retoriche, filler conversazionale. Classifica ogni fatto come `fact` (affermazione oggettiva/duratura), `preference` (gusto o scelta soggettiva dell'utente) oppure `episode` (evento specifico, puntuale, spesso datato). Se il testo non contiene alcun fatto utile, restituisci una lista vuota. Non inventare informazioni non presenti nel testo.
- **User prompt:**
  > Testo:
  > """{chunk_text}"""
  >
  > Estrai i fatti atomici secondo lo schema fornito.
- Parametri chiamata: `temperature=0` (determinismo), `response_format` = JSON Schema generato dal modello pydantic `FactExtractionResult` (§17.1).

---

## 6. Pipeline di dreaming (§3 di `milestone1.md`)

Endpoint: `POST /dreaming/run` → `{job_id?}` (se omesso, gira su tutti i fatti "freschi" non ancora passati dal dreaming, tracciati con un flag interno `dreamed: false` sul nodo `Fact`, resettato a `true` a fine ciclo per quel fatto).

### 6.1 Raggruppamento (§3.1) — via GDS

```cypher
// 1. proietta in memoria i fatti freschi con il loro embedding
CALL gds.graph.project(
  'freshFacts',
  { Fact: { properties: 'embedding', nodeFilter: 'n.dreamed = false' } },
  '*'
)

// 2. kNN sugli embedding → relazioni SIMILAR pesate per similarità coseno
CALL gds.knn.write('freshFacts', {
  nodeProperties: ['embedding'],
  topK: 10,
  similarityCutoff: 0.80,
  writeRelationshipType: 'SIMILAR',
  writeProperty: 'score'
})

// 3. weakly connected components sul grafo SIMILAR → gruppi
CALL gds.wcc.stream('freshFacts', { relationshipTypes: ['SIMILAR'] })
YIELD nodeId, componentId
RETURN componentId, collect(gds.util.asNode(nodeId).id) AS factIds
```

I fatti vengono raggruppati **prima per `doc_id`** (proiezione filtrata per documento, se il dreaming gira per-documento appena ingerito) **poi per vicinato di embedding** come sopra — coerente con §3.1. Ogni componente con ≥1 fatto è un gruppo; evento `group_formed` per componente. Al termine: `CALL gds.graph.drop('freshFacts')`.

> Nota versione: verificare la sintassi esatta (`gds.knn.write` vs `gds.knn.mutate`+`gds.wcc.write`) contro la versione di GDS installata; il pattern concettuale (kNN → WCC) è stabile dalla 2.x in poi.

### 6.2 Consolidamento + `derives` (§3.2)

Per ogni gruppo, una chiamata OpenAI (structured output) che fonde i fatti del gruppo. Due esiti, identici a `milestone1.md`:

- **Astrazione D** da pattern `S1..Sn` →
  ```cypher
  CREATE (d:Fact {id:$did, text:$text, type:$type, is_latest:true,
                   confidence:1.0, source_doc_id:$doc_id, embedding:$emb,
                   created_at:datetime(), dreamed:true})
  WITH d
  UNWIND $sourceIds AS sid
  MATCH (s:Fact {id: sid})
  CREATE (d)-[:DERIVES]->(s)
  WITH d, s
  MATCH (s)-[:DERIVED_FROM]->(ch:Chunk)
  MERGE (d)-[:DERIVED_FROM]->(ch)
  ```
  I fatti sorgente **non** vengono toccati su `is_latest` (restano quel che erano). Evento `fact_derived`.
- **Versione più pulita di un fatto esistente** → non scritta subito come nuovo nodo definitivo: passa come candidato N alla rilevazione relazioni (§6.3), esattamente come da `milestone1.md` §3.2.

**Prompt di consolidamento (structured output, schema `ConsolidationResult` in §17.2):**

- **System prompt:**
  > Sei un motore di consolidamento di fatti. Ricevi un gruppo di fatti semanticamente vicini estratti da una knowledge base. Se i fatti descrivono ripetizioni o frammenti di uno stesso pattern più generale, produci un'**astrazione** di livello più alto che sintetizza il pattern (`outcome="abstraction"`), elencando gli id di *tutti* i fatti sorgente usati. Se invece un fatto del gruppo è semplicemente una versione più chiara/pulita di un altro, senza costituire un pattern nuovo, produci la versione più pulita di quell'**unico** fatto (`outcome="cleaned_fact"`), lasciando `source_fact_ids` vuoto. Non inventare informazioni non presenti nei fatti forniti. Non fondere fatti che si contraddicono in un'unica affermazione: se noti una contraddizione, preferisci `cleaned_fact` sul fatto più recente/specifico e lascia che sia il passo successivo (classificazione relazioni) a gestirla.
- **User prompt:**
  > Fatti del gruppo:
  > {per ogni fatto: "- [{id}] {text}"}
  >
  > Produci il consolidamento secondo lo schema fornito.
- Parametri: `temperature=0`.

### 6.3 Rilevazione relazioni + catene + `is_latest` (§3.3, §5)

Per ogni fatto nuovo/consolidato **N**, ricerca candidati **solo fra `is_latest = true`** via vector index nativo:

```cypher
CALL db.index.vector.queryNodes('fact_embedding', 10, $n_embedding)
YIELD node AS candidate, score
WHERE candidate.is_latest = true AND candidate.id <> $n_id
RETURN candidate.id AS id, candidate.text AS text, score
```

Per ogni candidato **V**, una chiamata OpenAI classifica `replaces | extends | none` (structured output). Scrittura atomica in un'unica transazione per candidato, che copre sia l'arco sia il mantenimento incrementale di `is_latest` (§5 di `milestone1.md`):

```cypher
// esito "replaces"
MATCH (n:Fact {id:$n_id}), (v:Fact {id:$v_id})
CREATE (n)-[:UPDATES {created_at: datetime()}]->(v)
SET v.is_latest = false, n.is_latest = true
```
```cypher
// esito "extends"
MATCH (n:Fact {id:$n_id}), (v:Fact {id:$v_id})
CREATE (n)-[:EXTENDS {created_at: datetime()}]->(v)
```

**Prompt di classificazione relazione (structured output, schema `RelationClassification` in §17.3):**

- **System prompt:**
  > Confronta il FATTO NUOVO con il FATTO ESISTENTE e classifica la relazione tra i due:
  > - `"replaces"` se il fatto nuovo contraddice o sostituisce il fatto esistente (es. cambia un valore, un'informazione più recente annulla o rimpiazza la precedente sullo stesso soggetto/attributo).
  > - `"extends"` se il fatto nuovo aggiunge dettagli complementari, senza contraddire il fatto esistente: entrambi possono restare veri contemporaneamente.
  > - `"none"` se non c'è relazione significativa tra i due.
  >
  > Rispondi solo secondo lo schema fornito, senza aggiungere testo libero.
- **User prompt:**
  > FATTO NUOVO: "{n_text}"
  > FATTO ESISTENTE: "{v_text}"
  >
  > Classifica la relazione.
- Parametri: `temperature=0`. Nota: in questo milestone `replaces` è l'unico esito che genera `updates` — un conflitto genuino da preservare (`contradicts`) è esplicitamente fuori scope (`milestone1.md` §9) e va trattato come `replaces` per recency, come indicato nel prompt stesso implicitamente (non c'è un terzo esito "conflitto").
`none` → nessuna scrittura. Eventi `edge_created` e, se applicabile, `is_latest_changed`.

Poiché il candidate-set è filtrato a `is_latest = true`, N aggancia sempre la **testa** della catena `updates`, mai un nodo storico — è la stessa garanzia di `milestone1.md` §3.3/§5, qui ottenuta gratuitamente dal filtro nel vector index query.

---

## 7. `is_latest` — invariante e riconciliazione (§5 di `milestone1.md`)

Invariante (identica, riscritta in Cypher):

```cypher
// X.is_latest ⇔ NOT EXISTS ( (:Fact)-[:UPDATES]->(X) )
```

**Query di riconciliazione/canarino** (equivalente Cypher della `UPDATE` SQL di `milestone1.md` §5), da eseguire come test e come riparazione batch dopo ogni ciclo di dreaming:

```cypher
MATCH (f:Fact)
WITH f, NOT EXISTS { ()-[:UPDATES]->(f) } AS correct
WHERE f.is_latest <> correct
SET f.is_latest = correct
RETURN count(f) AS driftCount
```

`driftCount = 0` dopo il dreaming è il canarino di correttezza richiesto da `milestone1.md`. Il backend lo esegue automaticamente a fine di ogni `POST /dreaming/run` e lo espone nella risposta e come evento SSE `is_latest_reconciliation {driftCount}`.

---

## 8. Query engine (§6 di `milestone1.md`)

**8.1 Query corrente** — `POST /query {text, type_filter?}`:
1. embed della query (bge-base-en-v1.5);
2. `db.index.vector.queryNodes('fact_embedding', k, $emb) WHERE node.is_latest = true` (+ filtro `type` opzionale);
3. espansione via `EXTENDS` (solo vicini correnti) e `DERIVES` collegati:
   ```cypher
   MATCH (f:Fact) WHERE f.id IN $topKIds
   OPTIONAL MATCH (f)-[:EXTENDS]-(e:Fact {is_latest:true})
   OPTIONAL MATCH (f)-[:DERIVES]-(d:Fact)
   RETURN f, collect(DISTINCT e) AS extended, collect(DISTINCT d) AS derived
   ```
4. l'insieme (con provenienza `DERIVED_FROM → Chunk → source_doc_id`) va all'LLM per la risposta finale; la risposta HTTP include `{answer, facts_used, subgraph}` — `subgraph` è ciò che il frontend userà per evidenziare i nodi coinvolti nel Graph Explorer.

**8.2 Query storica** — `GET /facts/{id}/history`: risale la catena `updates` come **variable-length path nativo**, senza CTE ricorsiva:

```cypher
MATCH path = (current:Fact {id: $id})-[:UPDATES*0..]->(historical:Fact)
RETURN path ORDER BY length(path)
```

---

## 9. API backend (superficie REST + SSE)

| Metodo | Path | Descrizione |
|---|---|---|
| `POST` | `/documents` | Avvia ingestione di un documento → `{job_id}` |
| `GET` | `/events/stream?job_id=` | Stream SSE degli eventi pipeline (ingestione e/o dreaming) |
| `POST` | `/dreaming/run` | Avvia un ciclo di dreaming sui fatti "freschi" → `{job_id}` |
| `GET` | `/graph` | Nodi/relazioni per il Graph Explorer, filtri `is_latest`, `type`, `doc_id`, `limit` |
| `GET` | `/facts/{id}` | Dettaglio fatto: testo, tipo, confidence, is_latest, provenienza (chunk + doc) |
| `GET` | `/facts/{id}/history` | Catena storica risalendo `UPDATES` (§8.2) |
| `POST` | `/query` | Query NL → `{answer, facts_used, subgraph}` (§8.1) |
| `POST` | `/reconcile` | Esegue la query di riconciliazione `is_latest` (§7) fuori da un ciclo di dreaming, per test manuali |
| `GET` | `/health` | liveness/readiness (Neo4j reachable, GDS loaded) |

## 10. Schema eventi realtime (Pipeline Monitor)

Ogni evento SSE è `{ts, job_id, stage, event, payload}`. `stage ∈ {chunking, extraction, grouping, consolidation, relation_detection, reconciliation, done}`.

```json
{"ts":"...", "job_id":"...", "stage":"extraction", "event":"fact_extracted", "payload":{"fact_id":"...", "chunk_id":"...", "type":"fact"}}
{"ts":"...", "job_id":"...", "stage":"extraction", "event":"chunk_discarded_noise", "payload":{"chunk_id":"..."}}
{"ts":"...", "job_id":"...", "stage":"grouping", "event":"group_formed", "payload":{"component_id":3, "fact_ids":["...","..."]}}
{"ts":"...", "job_id":"...", "stage":"consolidation", "event":"fact_derived", "payload":{"fact_id":"...", "source_fact_ids":["...","..."]}}
{"ts":"...", "job_id":"...", "stage":"relation_detection", "event":"edge_created", "payload":{"type":"updates", "src":"...", "tgt":"..."}}
{"ts":"...", "job_id":"...", "stage":"relation_detection", "event":"is_latest_changed", "payload":{"fact_id":"...", "value":false}}
{"ts":"...", "job_id":"...", "stage":"reconciliation", "event":"drift_check", "payload":{"drift_count":0}}
{"ts":"...", "job_id":"...", "stage":"done", "event":"pipeline_complete", "payload":{"stats":{"chunks":12,"facts":9,"edges":4}}}
```

---

## 11. Frontend

### 11.1 Graph Explorer
- Libreria: **`@neo4j-nvl/react`** (renderer WebGL ufficiale Neo4j) — scelto perché produce uno stile "grafo Neo4j" nativo e coerente, gestisce bene styling per-property e layout force-directed su dataset di questa scala senza codice custom di rendering.
- Dati: `GET /graph` mappato al formato NVL `{nodes, relationships}`.
- **Codifica visiva** (per "visivamente coerente"):

| Elemento | Encoding |
|---|---|
| Nodo `Fact`, `type=fact` | cerchio pieno, colore primario |
| Nodo `Fact`, `type=preference` | cerchio pieno, colore secondario |
| Nodo `Fact`, `type=episode` | cerchio pieno, colore terziario |
| `is_latest = false` (storico) | nodo semi-trasparente + bordo tratteggiato |
| Relazione `UPDATES` | freccia piena, colore "warning" (indica sostituzione) |
| Relazione `EXTENDS` | freccia piena, colore "info" |
| Relazione `DERIVES` | freccia tratteggiata, colore "success" |
| Dimensione nodo | proporzionale a `confidence` (uniforme in questo milestone: tutti uguali finché §confidence resta a `1.0` fisso) |

- Interazione: click su nodo → pannello laterale con testo completo, `type`, `confidence`, `is_latest`, timestamp, provenienza (chunk + snippet documento sorgente via `DERIVED_FROM`); doppio click → `GET /facts/{id}/history`, evidenzia la catena `UPDATES` risalita nel grafo. Toggle in toolbar: "solo correnti" (`is_latest=true`) vs "includi storico".
- I nodi `Chunk` **non** sono mostrati nel grafo principale (evita clutter); sono accessibili solo dal pannello di dettaglio di un `Fact`.

### 11.2 Pipeline Monitor
- Si connette a `GET /events/stream?job_id=` con `EventSource`.
- Vista a step (chunking → estrazione → grouping → consolidamento → rilevazione relazioni → riconciliazione), ciascuno con contatore live e log espandibile degli eventi grezzi.
- Ogni evento che crea un nodo/arco fa un piccolo "pulse" evidenziato sul nodo corrispondente nel Graph Explorer se aperto in parallelo (stesso stato React condiviso via context/store, es. Zustand) — è il modo in cui si "vede" il grafo formarsi in tempo reale, non solo leggerne il log.

### 11.3 Query Panel
- Input NL → `POST /query`. Mostra `answer` con citazioni cliccabili verso i `facts_used`; il campo `subgraph` della risposta viene passato al Graph Explorer per evidenziare (highlight, non filtrare) i nodi/archi usati nella risposta, con dimming del resto del grafo.

### 11.4 Layout applicativo
Tre pannelli in un'unica dashboard (tab o split-view ridimensionabile): Graph Explorer al centro (superficie maggiore), Pipeline Monitor e Query Panel come pannelli laterali/inferiori richiudibili — così l'utente vede il grafo *mentre* la pipeline lo popola, invece di schermate separate scollegate.

---

## 12. Deployment (Docker Compose)

```yaml
services:
  neo4j:
    image: neo4j:5.24-community
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
      NEO4J_PLUGINS: '["graph-data-science"]'
      NEO4J_dbms_security_procedures_unrestricted: gds.*
    ports: ["7474:7474", "7687:7687"]
    volumes: ["neo4j_data:/data"]

  backend:
    build: ./backend
    environment:
      NEO4J_URI: bolt://neo4j:7687
      NEO4J_USER: neo4j
      NEO4J_PASSWORD: ${NEO4J_PASSWORD}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      OPENAI_MODEL: gpt-4o-mini
      EMBEDDING_MODEL: BAAI/bge-base-en-v1.5
    depends_on: [neo4j]
    ports: ["8000:8000"]

  frontend:
    build: ./frontend
    environment:
      NEXT_PUBLIC_API_URL: http://localhost:8000
    depends_on: [backend]
    ports: ["3000:3000"]

volumes:
  neo4j_data:
```

`.env.example` con `NEO4J_PASSWORD`, `OPENAI_API_KEY`.

---

## 13. Struttura repo

```
/backend
  /app
    /api          → routers: documents.py, dreaming.py, query.py, graph.py, events.py
    /core         → config.py, neo4j_client.py, event_bus.py
    /pipeline     → chunking.py, extraction.py, embeddings.py, grouping.py, consolidation.py, relations.py, reconcile.py
    /models       → schemi pydantic (fatti estratti, classificazione relazione, risposte API)
    main.py
  /tests          → pytest + testcontainers-neo4j
  pyproject.toml
/frontend
  /app            → route Next.js (dashboard unica, §11.4)
  /components     → GraphExplorer.tsx, PipelineMonitor.tsx, QueryPanel.tsx, FactDetailPanel.tsx
  /lib            → api-client.ts, useEventStream.ts (hook SSE), store.ts (Zustand)
docker-compose.yml
.env.example
```

---

## 14. Criteri di accettazione → verifica tecnica

> Questi sono i test di **integrazione** (richiedono Neo4j+GDS reale, via `testcontainers`). I test **unitari** che li precedono nella piramide di test (nessun DB, nessuna rete) sono in §19.

Mappatura diretta di `milestone1.md` §8 su questo stack:

| Criterio (`milestone1.md` §8) | Verifica su Neo4j |
|---|---|
| Ingestione crea chunk+fatti, provenienza corretta, rumore scartato | Test integrazione: dopo `POST /documents`, contare `(:Chunk)`, `(:Fact)-[:DERIVED_FROM]->(:Chunk)`; verificare che i chunk "rumore" non abbiano `Fact` collegati |
| Sostituzione → `UPDATES`, vecchio `is_latest=false`, query corrente restituisce solo il nuovo | Cypher assert su `EXISTS((n)-[:UPDATES]->(v))`, `v.is_latest=false`; `GET /query` non deve includere `v.id` |
| Catena `A←B←C` → solo C `is_latest=true`, storico percorribile | `MATCH path=(c)-[:UPDATES*]->(a)` deve restituire path di lunghezza 2; solo `c.is_latest=true` |
| `EXTENDS` → entrambi restano correnti, query li restituisce insieme | assert `is_latest=true` su entrambi dopo la scrittura dell'arco |
| Consolidamento → `DERIVES` verso sorgenti, sorgenti restano | assert nodo `D` con `DERIVES` verso `S1..Sn`, `Si.is_latest` invariato |
| Update su fatto già superato aggancia la testa, non lo storico | test mirato: crea catena `A←B`, poi fatto N semanticamente vicino ad A → deve produrre `UPDATES(N→B)`, mai `UPDATES(N→A)`, perché la ricerca candidati filtra `is_latest=true` |
| Riconciliazione non cambia righe dopo un ciclo di dreaming | eseguire la query di §7 subito dopo `POST /dreaming/run`: `driftCount` deve essere `0` |
| Query storica ricostruisce l'evoluzione | `GET /facts/{id}/history` restituisce l'intera catena in ordine |

Questi test vanno scritti come suite `pytest` di integrazione contro un container Neo4j+GDS effimero (`testcontainers`), eseguibili in CI locale prima di considerare il milestone "fatto".

---

## 15. Rischi, assunzioni, limiti noti

- **GDS su Community Edition**: gli algoritmi usati (`knn`, `wcc`) sono disponibili nella licenza Community; la concorrenza di esecuzione è limitata rispetto a Enterprise, non rilevante alla scala di un milestone/prototipo.
- **Concorrenza scritture `is_latest`**: ogni scrittura di arco + update di `is_latest` è racchiusa in un'unica transazione Cypher (§6.3) per evitare stati intermedi incoerenti; il dreaming gira comunque come job singolo, sequenziale, non parallelo su più worker in questo milestone.
- **Costo/latenza OpenAI**: mitigato raggruppando l'estrazione a livello di chunk (una call ciascuno, come da §2.2) e il consolidamento a livello di gruppo (§3.2), non per singola coppia di fatti.
- **Nessuna autenticazione**: app locale single-user, come da scope di `milestone1.md`; da aggiungere solo se il milestone successivo lo richiede.
- **Event bus in-process**: sufficiente per singolo worker Uvicorn; se in futuro si scala a più worker/processi serve un broker esterno (Redis pub/sub) — non necessario ora, isolato dietro l'interfaccia `event_bus.py`.
- **Dimensione embedding fissa (768, bge-base-en-v1.5)**: cambiare modello in futuro richiede ricreare gli indici vettoriali e ricalcolare tutti gli embedding esistenti; da tenere a mente se si valuta un cambio modello nei milestone successivi.

---

## 16. Ordine di implementazione

1. Docker Compose (Neo4j+GDS) + schema Cypher (§4.2) + smoke test connessione.
2. Backend skeleton FastAPI + driver Neo4j + config.
3. Ingestione: chunking → embedding locale → `POST /documents` (§5), eventi SSE minimi.
4. Estrazione fatti + filtro rumore (§5.2).
5. Dreaming: grouping GDS (§6.1) → consolidamento/`DERIVES` (§6.2) → relazioni + `is_latest` (§6.3, §7).
6. Query corrente + storica (§8), endpoint `/query` e `/facts/{id}/history`.
7. Frontend: Graph Explorer (NVL) collegato a `/graph`.
8. Frontend: Pipeline Monitor via SSE, collegato agli eventi di ingestione/dreaming.
9. Frontend: Query Panel con highlight del subgraph di risposta.
10. Suite `pytest` di accettazione (§14) contro Neo4j+GDS in `testcontainers`.

---

## 17. Schemi Pydantic (contratti dati precisi)

Ogni chiamata LLM usa **structured output** vincolato a uno di questi modelli (mai parsing di free-text). Sono anche i modelli usati per validare/serializzare le risposte delle API REST corrispondenti.

**17.1 Estrazione fatti (§5.2, §5.3)**
```python
class FactType(str, Enum):
    fact = "fact"
    preference = "preference"
    episode = "episode"

class ExtractedFact(BaseModel):
    text: str
    type: FactType

class FactExtractionResult(BaseModel):
    facts: list[ExtractedFact]   # lista vuota ammessa → chunk scartato come rumore
```

**17.2 Consolidamento (§6.2)**
```python
class ConsolidationOutcome(str, Enum):
    abstraction = "abstraction"     # → crea D con DERIVES verso source_fact_ids
    cleaned_fact = "cleaned_fact"   # → diventa candidato N per §6.3, source_fact_ids vuoto

class ConsolidationResult(BaseModel):
    outcome: ConsolidationOutcome
    text: str
    type: FactType
    source_fact_ids: list[str] = []

    @model_validator(mode="after")
    def check_sources(self):
        if self.outcome == ConsolidationOutcome.abstraction and not self.source_fact_ids:
            raise ValueError("abstraction richiede almeno un source_fact_id")
        return self
```

**17.3 Classificazione relazione (§6.3)**
```python
class RelationLabel(str, Enum):
    replaces = "replaces"
    extends = "extends"
    none = "none"

class RelationClassification(BaseModel):
    relation: RelationLabel
```

**17.4 Risposta query (§8.1, endpoint `POST /query`)**
```python
class FactUsed(BaseModel):
    id: str
    text: str
    source_doc_id: str

class SubgraphNode(BaseModel):
    id: str
    label: Literal["Fact"]
    properties: dict[str, Any]

class SubgraphRelationship(BaseModel):
    source: str
    target: str
    type: Literal["updates", "extends", "derives"]

class Subgraph(BaseModel):
    nodes: list[SubgraphNode]
    relationships: list[SubgraphRelationship]

class QueryResponse(BaseModel):
    answer: str
    facts_used: list[FactUsed]
    subgraph: Subgraph
```

Questi modelli vivono in `/backend/app/models/` (§13) e sono l'unica fonte di verità sia per il `response_format` mandato a OpenAI sia per la validazione delle risposte REST — evita che schema del prompt e schema dell'API divergano nel tempo.

---

## 18. Resilienza e gestione errori nelle chiamate LLM

Ogni chiamata OpenAI (estrazione §5.3, consolidamento §6.2, classificazione §6.3, risposta query §8.1) passa da un unico wrapper (`app/core/llm_client.py`) con questa policy, per non far fallire l'intera ingestione/dreaming per un singolo errore isolato:

- **Retry con backoff esponenziale** (libreria `tenacity`): max 3 tentativi, attese 1s/2s/4s, **solo** su errori transienti — timeout, rate limit (HTTP 429), errori 5xx del provider. Non si ritenta alla cieca su errori non transienti.
- **Timeout per chiamata**: 30s; oltre, l'errore è trattato come transiente (va a retry).
- **Validazione output**: anche con structured output il parsing pydantic può fallire (es. `ConsolidationResult` con `abstraction` senza sorgenti, §17.2). Un `ValidationError` **non** va a retry automatico (non è un problema di rete, ritentare produce lo stesso errore): l'elemento (chunk/gruppo/coppia) viene marcato `failed`, loggato con l'output grezzo del modello, emesso come evento SSE `llm_call_failed {stage, item_id, error}`, e la pipeline **continua** con gli elementi successivi del batch.
- **Concorrenza controllata**: le fasi con molte chiamate indipendenti (estrazione = 1 call/chunk, classificazione relazioni = 1 call/coppia N-candidato) usano un `asyncio.Semaphore` (default 5 richieste OpenAI parallele) per non superare i rate limit dell'account, invece di sparare tutte le chiamate in parallelo.
- **Costo/telemetria**: ogni risposta OpenAI espone `usage.total_tokens`; il backend li somma per `job_id` e li espone in `pipeline_complete` (§10) — utile per stimare il costo di un'ingestione o di un ciclo di dreaming.
- **Esito su fallimento persistente** (dopo i retry): il chunk resta senza fatti (trattato come se fosse rumore, ma loggato distintamente da "rumore genuino"); il gruppo di dreaming fallito non produce consolidamento ma i fatti del gruppo restano `dreamed: false` e vengono ritentati al prossimo ciclo di `POST /dreaming/run`; la coppia N-V fallita in classificazione semplicemente non produce arco (equivalente a `none`, ma loggata come `failed` non come `none` per distinguere i due casi in fase di debug).

---

## 19. Strategia di test — unit + integration

**19.1 Test unitari** (nessun Neo4j, nessuna chiamata di rete — mockare l'SDK OpenAI):
- **Chunking**: dato un testo noto, verificare dimensione ~256–512 token e overlap 10–15% dei chunk prodotti; caso limite testo più corto di un chunk.
- **Schemi Pydantic (§17)**: ciascun modello accetta input validi e rigetta input malformati (es. `type` non in enum, `abstraction` senza `source_fact_ids` → deve sollevare `ValidationError`, vedi §17.2).
- **Prompt builder**: dato un input di esempio, il prompt renderizzato contiene i placeholder correttamente sostituiti (testo del chunk, id/testo dei fatti nel gruppo, ecc.) — protegge da regressioni quando si modificano i template di §5.3/§6.2/§6.3.
- **Mapping esito → azione grafo**: funzione pura che, data una `RelationClassification` mockata, restituisce quale scrittura Cypher va eseguita (`replaces`→`UPDATES`+flip `is_latest`, `extends`→`EXTENDS`, `none`→nessuna scrittura) — testata senza toccare Neo4j.
- **Invariante `is_latest`**: funzione pura che, dato un insieme di archi `updates` in memoria (lista di coppie `(src, tgt)`), calcola quali nodi sono `is_latest` — replica in Python la logica di §7 per poterla testare in isolamento (unit test) oltre che a runtime (Cypher, test di integrazione).
- **Resilienza (§18)**: con l'SDK OpenAI mockato, verificare che il retry scatti su eccezioni transienti simulate (timeout, 429) e **non** scatti su `ValidationError`; verificare che dopo 3 fallimenti l'elemento sia marcato `failed` senza sollevare eccezione fuori dal wrapper.

**19.2 Test di integrazione**: la suite di §14, contro Neo4j+GDS reale in `testcontainers`. Copre il comportamento **end-to-end della semantica del grafo** (catene, `is_latest`, riconciliazione) che per definizione non si può verificare con soli mock.

La CI locale esegue prima §19.1 (veloce, nessuna dipendenza esterna), poi §19.2/§14 (più lenta, richiede Docker) solo se §19.1 passa.