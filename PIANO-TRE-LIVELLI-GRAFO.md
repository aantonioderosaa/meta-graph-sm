# Piano — Tre livelli dello stesso grafo (ordine / tempo / relazioni libere)

> Implementazione documentata in **Addendum 5** di `PIANO-GRAFO-EVENTI.md`.

## Context

Il grafo eventi oggi è **un solo disegno indifferenziato**: ordine di
esposizione, ordine cronologico e nessi causali/semantici finiscono tutti
negli stessi nodi/archi, con due limiti concreti già misurati in questa
sessione:

1. **La spina dorsale narrativa è invisibile a vista** (SEQUENZA non ha uno
   stile distinto dagli altri archi "dizionario").
2. **Gli eventi di zone diverse non sono quasi mai in relazione**: verificato
   sul grafo live — 10/10 `PRECEDE` nella stessa zona, 2/12 `COLLEGATO`
   cross-zona (e quei 2 sono solo il placeholder "ordine incerto" di Fase B,
   non un giudizio sul contenuto). `sentence_pair_linking.py`/
   `chiusura_temporale.py` girano **per singola zona** (`dedup.py`,
   chiamati da `espandi_zona` in `pipeline.py`) — una coppia di eventi
   a cavallo di due zone non viene **mai nemmeno guardata**, indipendentemente
   da quanto sia ovvio il nesso nel testo.
3. Verificato anche: `tempo_assoluto` è **0/23 eventi** — con l'estrazione
   attuale (`extract_event_entities`, Addendum 4) non si cattura più nessuna
   espressione temporale dal testo. Il motore di Allen (`allen.py`)
   gira ma è a secco di date reali.

### La proposta dell'utente

Non tre grafi separati: **lo stesso grafo persistito, tre proiezioni/viste
diverse per tre compiti diversi**:

- **Livello 1 — Ordine**: solo relazione di ordine di ingestione/menzione.
  Le zone (già segmentate da `zona_segmentation.py`) diventano nodi-hub
  su una linea orizzontale centrale ("il tronco"); ogni zona porta con sé,
  attaccato sopra o sotto in alternanza, il proprio sottografo di eventi. Il
  collegamento fra uno zona-hub e il successivo trasporta un **riassunto del
  cambio di contesto**, generato avendo in mano entrambi i riassunti di zona.
- **Livello 2 — Tempo**: un modello riceve tutto il testo (non importa come
  viene diviso internamente) e: (a) assegna date/espressioni relative dove
  disponibili; (b) individua eventi **contemporanei** anche senza data
  esplicita, tramite una nuova relazione minima "stesso momento"; (c) etichetta
  ogni cluster risultante con la propria data o arco temporale (esplicito o
  simbolico). Solo relazioni temporali in questa vista.
- **Livello 3 — Relazioni libere**: il modello confronta gli eventi
  nell'intera storia (non per coppie adiacenti, non per zona) e assegna
  liberamente le relazioni di significato (causa, condizione, scopo,
  concessione, contrasto, limite, contenuto) leggendo una **spiegazione
  semantica di ogni tipo di relazione**, non un elenco di connettivi da
  cercare grammaticalmente. Risolve direttamente il limite (2) sopra.

### Decisioni di disegno (prese qui, non richieste ulteriori conferme)

- **D1 — Livello 3 è additivo, non sostitutivo.** `sentence_pair_linking.py`
  resta **invariato** (continua a produrre CAUSA/CONDIZIONE/... per-zona,
  come oggi) — non lo tocchiamo per non rompere i suoi test e per tenere un
  segnale di confronto. Gli archi del nuovo Livello 3 portano `livello:'3'`
  in `props`; le viste filtrano per `livello` quando serve. Nessun doppione
  pericoloso: sono due segnali distinti, non due scritture dello stesso arco.
- **D2 — Un solo nuovo tipo di relazione per il Livello 2**: `CONTEMPORANEO`
  ("stesso momento", non direzionale nell'uso anche se persistito come arco
  diretto per uniformità di schema). Tutto il resto del Livello 2 (date
  esplicite, `PRECEDE`, algebra di Allen) riusa la macchina già esistente
  (`temporal_placement.py`, `allen.py`) — qui aggiungiamo solo
  il **segnale di input** che oggi manca (l'estrazione non cattura più
  espressioni temporali) e il **nodo cluster con etichetta**.
- **D3 — Riassunto di transizione per zona**: una relazione dedicata
  `:Zona-[:SUCCESSIONE_ZONA]->:Zona`, **una per ogni coppia ordinale-adiacente**
  (garantita, indipendente da cosa decide il classificatore macro esistente
  in `zona_edges.py`, che potrebbe non emettere nulla fra due zone). Porta
  `riassunto_transizione` generato con **entrambi** i riassunti di zona in
  input.
- **D4 — Le tre viste sono un parametro sulla rotta esistente**:
  `GET /event-graph/graph?vista=ordine|temporale|relazioni|tutto`, default
  `tutto` = comportamento attuale invariato (nessuna rottura).
- **D5 — Livello 2 e 3 sono chiamate "a documento intero"**, non per
  frase/zona: un solo prompt con la lista `id | span` di tutti gli eventi già
  estratti (stesso pattern già usato in `answer.py` per citare id reali),
  più il testo integrale per il Livello 2. Cap di sicurezza
  (`LIVELLO_MAX_EVENTI_PER_CHIAMATA`) con fallback a più chiamate solo se il
  documento è grande — stesso spirito di `EVENT_GRAPH_TEMPORAL_MAX_CANDIDATES`.
- **D6 — Isolamento (D6 del piano originale) rispettato**: nessuna nuova
  dipendenza; tutte le nuove chiamate passano da
  `app.pipeline.event_graph.infra.llm.call_structured`; tutte best-effort
  (falliscono in modo silenzioso e non bloccante, stesso pattern di
  `answer.py` — mai un'eccezione che affossa l'intera ingestione).

---

## PARTE A — Livello 1: Ordine (zone-tronco + riassunto di transizione)

### A1. Modello — `models/event_graph.py`

```python
class TransizioneZonaResult(BaseModel):
    riassunto: str  # cosa cambia nel contesto passando da zona A a zona B
```

Aggiungere `"SUCCESSIONE_ZONA"` come costante di modulo in `zona_edges.py`
(non in `TipoRelazione` — quell'enum è per archi Evento↔Evento/Menzione; gli
archi Zona-Zona già oggi non ci passano, vedi `zona_edges.ArcoZona`).

### A2. Nuovo modulo — `zona_transizioni.py`

```python
"""D3 — un riassunto di transizione per ogni coppia di zone ordinale-adiacenti.

Indipendente dal classificatore macro (zona_edges.py): questo arco esiste
sempre, per ogni confine, e porta solo testo, mai un tipo di relazione.
"""
SYSTEM_TRANSIZIONE = """Leggi i riassunti di due porzioni consecutive di una
narrazione. Scrivi 1-2 frasi su COSA CAMBIA nel contesto passando dalla prima
alla seconda (nuovi personaggi, nuovo luogo, nuova fase dell'azione, nuovo
argomento). Non riassumere il contenuto, descrivi il cambiamento. Italiano o
inglese, la stessa lingua del testo. Temperatura 0."""

async def genera_transizione(zona_a: Zona, zona_b: Zona, *, job_id=None) -> TransizioneZonaResult | None:
    # best-effort: eccezione -> None (stesso pattern di answer.sintetizza_risposta)
    ...

async def genera_transizioni_zona(zone: list[Zona], *, job_id=None) -> dict[tuple[str,str], str]:
    """Una chiamata per ogni coppia (zona[i], zona[i+1]) con zona.espansa=True."""
    ...
```

Input a `genera_transizione`: `zona_a.riassunto` + `zona_b.riassunto` (già
prodotti da `zona_summary.riassumi_zone`, M1 macro) — **mai** il testo
grezzo integrale (i riassunti bastano e costano meno).

### A3. Persistenza — `persistence.py`

Nuova `_merge_successione_zona(session, id_a, id_b, riassunto, job_id)`:
`MERGE (za:Zona {id:$id_a})-[r:SUCCESSIONE_ZONA]->(zb:Zona {id:$id_b}) SET
r.riassunto_transizione=$riassunto, r.regola=..., r.versione_regole=...`.
Nuova `persisti_transizioni_zona(session, transizioni: dict, job_id)` chiamata
da `pipeline.py`.

### A4. Vista Livello 1 — `catalog.py`

Nuova `async def grafo_livello1(session, documento) -> dict`:

- Nodi: `:Zona` (id, ordinale, riassunto, evento_centrale) **+** i propri
  `:Evento` come figli (Cytoscape compound node: `data.parent = zona.id`).
- Archi: `SUCCESSIONE_ZONA` fra zone consecutive (con `riassunto_transizione`
  in `data`) **+** solo gli archi evento-evento di ordine
  (`SEQUENZA`, `COLLEGATO`) dentro ciascuna zona — niente CAUSA/CONTRASTO/ecc.
  in questa vista, per costruzione della query (`WHERE type(r) IN
  ['SEQUENZA','COLLEGATO']`).
- Il ponte cross-zona già esistente (`pipeline.collega_dorsale_zone`)
  resta **il collegamento fra le code di eventi**, non fra gli zona-hub:
  restano due cose distinte e compatibili (trunk fra zone, filo fra eventi).

### A5. Frontend — layout a tronco

`EventGraphPanel.tsx`: nuova opzione di layout "Ordine" — preset (non
forza-diretto): zona-hub in riga (`y=0`, `x` crescente per `ordinale`),
ogni zona-figlio-eventi disposto in colonna sopra (`ordinale` pari) o sotto
(`ordinale` dispari) del proprio hub, usando i compound node di Cytoscape
(già supportato nativamente, zero nuove dipendenze). Il riassunto di
transizione compare come tooltip/etichetta sull'arco `SUCCESSIONE_ZONA`.

### Acceptance — Parte A

| # | Verifica | Atteso |
|---|---|---|
| A-u1 | Unit `genera_transizione` con 2 `Zona` stub + `call_structured` stub | ritorna `TransizioneZonaResult.riassunto` non vuoto |
| A-u2 | `genera_transizioni_zona` con LLM che fallisce su una coppia | quella coppia → nessuna voce nel dict, le altre coppie non sono affette (best-effort per-coppia) |
| A-u3 | `_merge_successione_zona` idempotente | rieseguito 2× → stesso arco, nessun duplicato |
| A-e1 | Re-ingest `sole-e-vento` (5 zone) | `MATCH (:Zona)-[r:SUCCESSIONE_ZONA]->(:Zona) RETURN count(r)` = **4** (una per confine) |
| A-e2 | Ogni `SUCCESSIONE_ZONA` ha `riassunto_transizione` non vuoto | verificato via Cypher |
| A-e3 | `GET /event-graph/graph?vista=ordine&documento=doc-1` | nodi `:Zona` presenti, ogni `:Evento` ha `data.parent` = propria zona, **zero** archi CAUSA/CONTRASTO/CONDIZIONE/SCOPO/CONCESSIONE/LIMITE/CONTENUTO nel payload |
| A-e4 | Frontend: 5 zona-hub in riga, eventi a rami alternati sopra/sotto | verifica visiva manuale |

---

## PARTE B — Livello 2: Tempo (estrazione a documento intero + cluster)

### B1. Modello — `models/event_graph.py`

```python
class SegnaleTemporaleEvento(BaseModel):
    evento_id: str
    tempo_assoluto: str | None = None        # ISO YYYY / YYYY-MM / YYYY-MM-DD / intervallo, se risolvibile
    espressione_relativa: str | None = None  # testo grezzo ("2 giorni dopo"), se non risolvibile ad ISO
    contemporaneo_a: list[str] = Field(default_factory=list)  # altri evento_id allo stesso momento

class ClusterTemporaleProposto(BaseModel):
    etichetta: str    # "1994", "il giorno del litigio", "poco dopo l'arrivo del viandante"
    tipo: Literal["data_esplicita", "intervallo", "relativo", "simbolico"]
    eventi: list[str]  # evento_id membri

class LivelloTemporaleResult(BaseModel):
    segnali: list[SegnaleTemporaleEvento] = Field(default_factory=list)
    cluster: list[ClusterTemporaleProposto] = Field(default_factory=list)
```

Aggiungere `"CONTEMPORANEO"` a `TipoRelazione`.

### B2. Nuovo modulo — `livello_temporale.py`

```python
SYSTEM_LIVELLO_TEMPORALE = """Leggi l'intero testo e la lista di eventi già
identificati (id + frase). Per ciascun evento:
- se il testo dà una data/ora/periodo esplicito, riportalo come tempo_assoluto
  (ISO, granularità libera: anno / mese / giorno / intervallo);
- se dà solo un'espressione relativa non risolvibile ("il giorno dopo", "due
  anni prima") senza un ancoraggio assoluto nel testo, riportala in
  espressione_relativa;
- se il testo indica che due o più eventi avvengono ALLO STESSO MOMENTO anche
  senza data (es. "mentre X faceva questo, Y faceva quello", "nello stesso
  istante"), collega i loro id in contemporaneo_a — è un giudizio di
  significato, non serve una parola chiave specifica.
Poi proponi cluster: raggruppa eventi con la stessa collocazione temporale
(stessa data, stesso "momento", o stesso ancoraggio relativo) e dai a ogni
gruppo un'etichetta leggibile (una data se c'è, altrimenti una descrizione
breve del momento). Ogni evento appartiene esattamente a un cluster.
Non inventare date che il testo non dà. Usa solo gli id forniti.
Italiano o inglese. Temperatura 0."""

def user_livello_temporale(testo_documento: str, eventi: list[EventoRisolto]) -> str:
    """testo integrale + 'id | span' per ogni evento, ordinati per esposizione."""

async def estrai_livello_temporale(
    testo_documento: str, eventi: list[EventoRisolto], *, job_id=None
) -> LivelloTemporaleResult | None:
    """Best-effort, una chiamata (fino a LIVELLO_MAX_EVENTI_PER_CHIAMATA eventi;
    oltre, spezza in finestre consecutive per posizione e fonde i risultati —
    stesso spirito del cap di temporal_placement)."""
```

`LIVELLO_MAX_EVENTI_PER_CHIAMATA = 60` (costante di modulo, non `Settings` —
stesso stile di `EVENT_TRIAGE_MAX_SEARCH_QUERIES` nel dominio legacy).

### B3. Risoluzione deterministica — stesso modulo

- `evento_id` nella risposta deve appartenere all'insieme dato → scarta
  silenziosamente id inventati (mai creare riferimenti a eventi inesistenti,
  stesso principio guardia di `event_slots.apply_validated_slot` nel dominio
  legacy).
- Un evento senza `tempo_assoluto`/`espressione_relativa`/cluster esplicito →
  resta senza collocazione (non forzare un cluster "vuoto").
- `tempo_assoluto` risolto qui **si scrive come append** su
  `Evento.tempo_assoluto_revisioni` (campo già esistente, mai toccato finora
  dall'estrazione attuale) — coerente con la §14 "append-only, valore
  corrente = ultimo".

### B4. Persistenza — `persistence.py`

- Nuovo nodo `:ClusterTemporale {id, documento, etichetta, tipo, regola,
  versione_regole}` — id content-addressed: `cluster_temporale_id(doc_id,
  etichetta, tipo)` in `ids.py` (stesso schema di `zona_id`/`evento_id`).
- Arco `(:Evento)-[:APPARTIENE_A]->(:ClusterTemporale)` — `APPARTIENE_A`
  aggiunto a `TipoRelazione` (mero elenco chiuso per la sicurezza delle
  query, nessun impatto sul comportamento esistente).
- Arco `(:Evento)-[:CONTEMPORANEO]->(:Evento)` per le coppie esplicite oltre
  al clustering.
- `SET e.tempo_assoluto = ..., e.tempo_assoluto_revisioni = e.tempo_assoluto_revisioni + [...]`
  quando risolto.

Schema (`schema.cypher`, additivo):
```
CREATE CONSTRAINT eg_cluster_temporale_id IF NOT EXISTS FOR (c:ClusterTemporale) REQUIRE c.id IS UNIQUE;
CREATE INDEX eg_cluster_temporale_doc     IF NOT EXISTS FOR (c:ClusterTemporale) ON (c.documento);
```

### B5. Vista Livello 2 — `catalog.py`

`async def grafo_livello2(session, documento) -> dict`: nodi `:ClusterTemporale`
+ i loro `:Evento` (compound, `data.parent` = cluster) + archi `PRECEDE`
(quelli con `superato_da IS NULL`) e `CONTEMPORANEO`. Nessun altro tipo
d'arco in questa vista.

### B6. Frontend — layout a timeline

Layout `dagre` `rankdir: 'LR'` con i cluster ordinati per data risolta (i
simbolici/relativi ancorati al cluster più vicino che li referenzia, altrimenti
in coda) — riuso del layout dagre già presente (`cytoscape-dagre.d.ts`),
zero nuove dipendenze.

### Acceptance — Parte B

| # | Verifica | Atteso |
|---|---|---|
| B-u1 | `estrai_livello_temporale` con testo che ha una data esplicita ("nel 1990") | il `SegnaleTemporaleEvento` di quell'evento ha `tempo_assoluto` valorizzato |
| B-u2 | Testo con "mentre X, Y" | almeno un `contemporaneo_a` popolato fra i due eventi |
| B-u3 | Risposta con un `evento_id` inventato | scartato silenziosamente, nessun crash, nessun nodo/arco fantasma scritto |
| B-u4 | Documento con >60 eventi (stub) | più chiamate in finestre, risultati fusi senza duplicare cluster con la stessa etichetta |
| B-u5 | Fallimento LLM | `estrai_livello_temporale` ritorna `None`, l'ingestione prosegue (non solleva) |
| B-e1 | Re-ingest `sole-e-vento` | `MATCH (c:ClusterTemporale) RETURN count(c)` > 0; ogni evento ha 0 o 1 `APPARTIENE_A` |
| B-e2 | `GET /event-graph/graph?vista=temporale&documento=doc-1` | solo nodi Evento+ClusterTemporale, solo archi PRECEDE/CONTEMPORANEO |
| B-e3 | Nessuna data inventata | ogni `tempo_assoluto` scritto ha un `espressione_relativa`/testo di supporto tracciabile in `tempo_assoluto_revisioni` (provenienza) |

---

## PARTE C — Livello 3: Relazioni libere a documento intero

### C1. Modello — `models/event_graph.py`

```python
TipoRelazioneLibera = Literal[
    "CAUSA", "CONDIZIONE", "SCOPO", "CONCESSIONE", "CONTRASTO", "LIMITE", "CONTENUTO"
]

class RelazioneLibera(BaseModel):
    da_id: str
    a_id: str
    tipo: TipoRelazioneLibera
    spiegazione: str  # audit: perché il modello ha scelto questo tipo

class LivelloRelazioniResult(BaseModel):
    relazioni: list[RelazioneLibera] = Field(default_factory=list)
```

### C2. Nuovo modulo — `livello_relazioni.py`

```python
SYSTEM_LIVELLO_RELAZIONI = """Hai l'elenco completo degli eventi di una storia
(id + frase), in ordine di esposizione. Confrontali fra loro nel contesto
dell'intera storia — non solo eventi vicini — e assegna le relazioni di
significato che riconosci, usando ESCLUSIVAMENTE questi tipi:

- CAUSA: un evento provoca, spiega il motivo di, o è la ragione dell'altro.
- CONDIZIONE: un evento accade solo se l'altro si verifica (rapporto
  ipotetico/condizionale fra i due).
- SCOPO: un evento avviene AFFINCHÉ l'altro si realizzi (finalità).
- CONCESSIONE: un evento avviene NONOSTANTE l'altro (contrasto atteso ma
  disatteso).
- CONTRASTO: i due eventi si oppongono o divergono, senza rapporto di
  finalità o concessione.
- LIMITE: un evento vale FINCHÉ l'altro non si verifica (confine temporale
  di validità, non ordine puro).
- CONTENUTO: un evento è ciò che viene detto/pensato/deciso nell'altro
  (l'altro è un atto di dire/pensare/decidere che ha il primo come oggetto).

Non ti do una lista di connettivi da cercare: leggi il significato. Non
inventare relazioni deboli o di sola co-presenza/successione — quelle sono
gestite altrove. Ogni relazione richiede una spiegazione breve. Usa solo gli
id forniti, mai inventarne. Italiano o inglese. Temperatura 0."""

def user_livello_relazioni(eventi: list[EventoRisolto]) -> str:
    """'id | span' per ogni evento, in ordine di esposizione — mai il testo
    grezzo (il significato deve emergere dagli eventi, non da un nuovo giro
    di lettura del documento)."""

async def estrai_livello_relazioni(
    eventi: list[EventoRisolto], *, job_id=None
) -> LivelloRelazioniResult | None:
    """Best-effort, finestre da LIVELLO_MAX_EVENTI_PER_CHIAMATA se necessario."""
```

Nota deliberata: niente cap di confidenza, niente gate anti-CAUSA-inventata
lessicale (quelli restano solo dentro `sentence_pair_linking.py`/`event_edges.py`,
il meccanismo del Livello 1/legacy) — qui la libertà è la richiesta esplicita
dell'utente. L'unico guardrail resta strutturale: id validi, tipo nel vocabolario
chiuso, aciclicità di CAUSA verificata a valle (riuso di
`event_edges._causa_reaches`/logica equivalente prima di persistere).

### C3. Persistenza — `persistence.py`

`(:Evento)-[r:<TIPO>]->(:Evento) SET r.livello='3', r.regola=...,
r.versione_regole=..., r.spiegazione=$spiegazione`. Ciclo `CAUSA` rilevato →
non scritto, `:Quarantena {motivo:"ciclo CAUSA livello 3"}` (stessa politica
di §8.4/Q-d).

### C4. Vista Livello 3 — `catalog.py`

`async def grafo_livello3(session, documento) -> dict`: tutti gli `:Evento`
del documento (nessun raggruppamento/compound) + solo gli archi con
`livello='3'`. Nessuna zona, nessun cluster temporale in questa vista.

### C5. Frontend — layout forza-diretta

Layout `cose` (incluso nel core di Cytoscape.js, zero nuove dipendenze) —
adatto a un grafo non gerarchico. L'etichetta `spiegazione` in tooltip
sull'arco.

### Acceptance — Parte C

| # | Verifica | Atteso |
|---|---|---|
| C-u1 | Due eventi di **zone diverse** con nesso causale ovvio nello stub | relazione CAUSA generata fra loro (dimostra il fix del limite "mai fra zone diverse") |
| C-u2 | Risposta con `tipo` fuori vocabolario | scartata, non persistita, nessun crash |
| C-u3 | Risposta che chiuderebbe un ciclo CAUSA | arco non scritto, `:Quarantena` con motivo tracciato |
| C-u4 | Id inventato nella risposta | scartato silenziosamente |
| C-e1 | Re-ingest `sole-e-vento` | `MATCH (a:Evento)-[r]->(b:Evento) WHERE r.livello='3' AND a.chunk_id <> b.chunk_id RETURN count(*)` > 0 — **prova diretta** che il Livello 3 collega eventi di zone diverse, cosa che oggi non succede mai |
| C-e2 | `GET /event-graph/graph?vista=relazioni&documento=doc-1` | solo eventi + archi `livello='3'`, nessun nodo Zona/ClusterTemporale |
| C-e3 | Confronto con `sentence_pair_linking` esistente | i test `m17`/`sentence_pair_linking` restano verdi invariati (D1: additivo, non sostitutivo) |

---

## PARTE D — Trasversale

### D-1. `pipeline.py` — punto di innesco

In `run_event_graph_ingestion`, subito dopo `collega_dorsale_zone(sotto, zone)`
e prima di `_fase_b_if_available`:

```python
collega_dorsale_zone(sotto, zone)                                   # esistente
transizioni = await genera_transizioni_zona(zone, job_id=job_id)      # Parte A
livello_tempo = await estrai_livello_temporale(text, sotto.eventi, job_id=job_id)   # Parte B
livello_rel = await estrai_livello_relazioni(sotto.eventi, job_id=job_id)           # Parte C
async with _maybe_session(session) as persist_session:
    if persist_session is not None:
        await persistence.persisti_transizioni_zona(persist_session, transizioni, job_id)
        await persistence.persisti_livello_temporale(persist_session, livello_tempo, doc_id, job_id)
        await persistence.persisti_livello_relazioni(persist_session, livello_rel, job_id)
await _fase_b_if_available(session, sotto, job_id, doc_id)
```

Tutte e tre le chiamate sono **indipendenti fra loro** (nessuna dipende
dall'output delle altre) — possono girare in `asyncio.gather` se si vuole
ridurre la latenza totale; non obbligatorio per la correttezza.

### D-2. `catalog.py` — dispatch vista

`GET /event-graph/graph` guadagna `vista: Literal["tutto","ordine","temporale","relazioni"] = "tutto"`.
`vista="tutto"` → comportamento attuale **byte-per-byte identico** (nessuna
regressione). Le altre tre delegano a `grafo_livello1/2/3`.

### D-3. `catalog.py` — legenda

`GET /event-graph/catalog` guadagna una sezione per vista: quali tipi
d'arco/nodo compaiono in quale livello, cosa significa `CONTEMPORANEO`,
`SUCCESSIONE_ZONA`, `APPARTIENE_A`.

### D-4. Documentazione

`PIANO-GRAFO-EVENTI.md` (se ricostituito) o questo stesso documento — le tre
viste, le decisioni D1-D6 sopra, la tabella spec→modulo aggiornata con
`zona_transizioni.py`, `livello_temporale.py`, `livello_relazioni.py`.

---

## Macrotask (ordine di esecuzione)

| MT | Contenuto | Dipende da |
|---|---|---|
| **MT1** | Modelli: `TransizioneZonaResult`, `SegnaleTemporaleEvento`, `ClusterTemporaleProposto`, `LivelloTemporaleResult`, `RelazioneLibera`, `LivelloRelazioniResult`; `TipoRelazione` +`CONTEMPORANEO`+`APPARTIENE_A`; `ids.cluster_temporale_id` | — |
| **MT2** | `schema.cypher`: `:ClusterTemporale` constraint+index | MT1 |
| **MT3** | Parte A: `zona_transizioni.py` + persistenza `SUCCESSIONE_ZONA` + wiring pipeline | MT1 |
| **MT4** | Parte A: `grafo_livello1` in `catalog.py` + `vista=ordine` sulla rotta | MT3 |
| **MT5** | Parte A: frontend layout a tronco (compound zona, rami alternati) | MT4 |
| **MT6** | Parte B: `livello_temporale.py` (estrazione + risoluzione + finestre) | MT1, MT2 |
| **MT7** | Parte B: persistenza `ClusterTemporale`/`APPARTIENE_A`/`CONTEMPORANEO`/`tempo_assoluto_revisioni` + wiring pipeline | MT6 |
| **MT8** | Parte B: `grafo_livello2` + `vista=temporale` | MT7 |
| **MT9** | Parte B: frontend layout timeline | MT8 |
| **MT10** | Parte C: `livello_relazioni.py` (estrazione + guardrail ciclo/id/vocab) | MT1 |
| **MT11** | Parte C: persistenza archi `livello='3'` + quarantena ciclo + wiring pipeline | MT10 |
| **MT12** | Parte C: `grafo_livello3` + `vista=relazioni` | MT11 |
| **MT13** | Parte C: frontend layout forza-diretta (`cose`) | MT12 |
| **MT14** | `catalog.py`: legenda per vista | MT4, MT8, MT12 |
| **MT15** | Test: unit (A-u/B-u/C-u) + E2E su `sole-e-vento` (A-e/B-e/C-e) | MT5, MT9, MT13 |
| **MT16** | Documentazione: Addendum sul piano evento-grafo (o questo documento aggiornato) | MT15 |

Percorso critico: MT1→MT2 in serie; MT3→4→5, MT6→7→8→9, MT10→11→12→13 in
parallelo fra loro (tre rami indipendenti, coerente con D5); MT14 dopo i tre
rami; MT15 alla fine; MT16 per ultimo.

## Verifica end-to-end (ordine)

1. `pytest` sui nuovi file di unit test per modulo (A-u1..3, B-u1..5, C-u1..4).
2. `cd backend && python -m pytest tests/test_event_graph_*.py -q` — verde,
   nessuna regressione sui livelli/moduli esistenti (D1 garantisce che
   `sentence_pair_linking`/`event_edges`/`chiusura_temporale` restano intatti).
3. `python _wipe_eg.py && python _ingest_and_wait.py && python _dump_sole_vento.py`
   poi le query Cypher A-e1/A-e2, B-e1/B-e3, C-e1 sopra.
4. `curl /event-graph/graph?vista=ordine|temporale|relazioni|tutto` — quattro
   risposte, verificare la forma di ciascuna (A-e3, B-e2, C-e2) e che
   `vista=tutto` sia identica a prima di questo piano.
5. Frontend: tre viste selezionabili, verifica visiva dei tre layout.
6. `graphify update .`.
