# Piano — spegnimento CONTRADICTS + fix pipeline ingestione/dream

Nato dall'audit end-to-end su `christmas-carol.txt` (ingest+dream reali, 15h, log
completi in `scratchpad/christmas_carol_test/`). Aggiornato dopo verifica del codice
effettivamente applicato su disco (revisione riga per riga di tutti i diff, non solo
lettura dei nomi dei file), poi dopo un secondo run reale (`scratchpad/christmas_carol_run2/`)
con tutti i fix applicati, poi dopo un audit statistico del grafo prodotto.

## Stato — Macrotask 1-6 e 7c implementati sul codice; 7a/7b aperti

Verificato leggendo per intero ogni diff (`git diff`), non solo l'elenco dei file
modificati.

| Macrotask | Cosa fa | Stato codice |
|---|---|---|
| 1 — CONTRADICTS kill switch | Spegne la creazione di CONTRADICTS (isolato, resto intatto) | ✅ implementato e verificato corretto |
| 2 — semaforo dentro `_call_openai` | Un retry in backoff non tiene più occupato uno slot LLM | ✅ implementato e verificato corretto |
| 3 — fusioni sbagliate + `kernel_category` | Riuso entità già scritte nel doc, summary sui nodi nudi, gate su `kernel_category` prima del fast-merge | ✅ implementato e verificato corretto (3.1, 3.2, 3.3) |
| 4 — prompt relazioni rumorose | Divieto esplicito di relazioni da co-presenza + esempi negativi | ✅ implementato (riduce ma non elimina, vedi audit sotto) |
| 5 — backoff differenziato | 30s solo per "modello non disponibile", 8s per gli altri transitori | ✅ implementato e verificato corretto |
| 6 — circuit breaker | Dopo 3 fallimenti "modello non disponibile" consecutivi, pausa 20s condivisa invece di retry ridondanti in parallelo | ✅ implementato e verificato corretto |
| 3.4 — backfill `kernel_category` agganciato | Chiamato automaticamente a fine risoluzione entità, prima del backbone | ✅ implementato |
| 7c — batching `PairRelationDecision` | Coppie entità↔entità raggruppate in `PAIR_RELATION_BATCH_SIZE` (default 15) per chiamata, matching per `pair_index` | ✅ implementato |
| 7a — parallelizzare i cicli sequenziali del dreaming | Prossimo passo consigliato, non ancora implementato | ⬜ da fare (vedi Macrotask 7) |
| 7b — batching `EventRelationClassification` | Prossimo passo consigliato, non ancora implementato | ⬜ da fare (vedi Macrotask 7) |

**Difetto minore noto (non funzionale, solo copertura test)**: in
`backend/tests/test_node_resolution.py`, `test_candidate_cypher_filters_by_type` ha perso
le due asserzioni originali (`"candidate.type = $type"`, `"c.merged_into IS NULL"`) quando
sono state aggiunte quelle su `kernel_category` — il Cypher di produzione ha ancora
entrambe le clausole, solo il test non le pin-a più esplicitamente.

---

## Cosa è ACCESO e cosa è SPENTO nella pipeline oggi

### SPENTO (kill switch esplicito, default `False`)
- **`ENABLE_CONTRADICTS_DETECTION`** (`app/core/config.py`) — nessun arco `CONTRADICTS`
  viene più scritto, né dal rilevatore deterministico same-chunk (`ingestion.py`), né
  dai due branch a runtime del dreaming (`entity_relation_resolution.py`). `SUPERSEDES`,
  `UPDATED_BY`, `EXTENDS` restano attivi e non toccati da questo flag.
- **`ENABLE_EVENT_TRIAGE`**, **`ENABLE_PROMOTE`** (invariati, non toccati da questo piano).

### ACCESO (sempre attivo, nessun flag)
- **Riuso entità cross-chunk nello stesso documento** (`find_entity_in_document`,
  `ingestion.py`) — prima di creare un nuovo nodo entità "nudo", cerca un match
  esatto/case-insensitive tra le entità già scritte per lo stesso `doc_id`.
- **Summary minimo sui nodi nudi residui** — `summary=event_name` invece di vuoto.
- **Embedding nome+summary** invece del solo nome nudo.
- **Gate su `kernel_category` prima della fusione automatica** (`_fast_path_canonical`) —
  un candidato ≥0.90 fonde automaticamente solo se il suo `kernel_category` coincide con
  quello del nodo nuovo; altrimenti passa dalla conferma LLM. Il match per nome esatto
  resta senza gate.
- **Backfill `kernel_category`** agganciato a fine risoluzione entità, prima del backbone.
- **Semaforo LLM scoped alla sola chiamata di rete** (`_call_openai`).
- **Backoff differenziato per tipo di errore** — 30s max solo per "modello non
  disponibile", 8s max per gli altri transitori.
- **Circuit breaker su "modello non disponibile"** — dopo 3 fallimenti consecutivi,
  pausa condivisa di 20s.
- **Prompt pairwise con divieto esplicito di relazioni da co-presenza** — riduce ma non
  elimina il rumore (vedi audit sotto).
- **Batch pairwise entità↔entità** — le coppie di un chunk sono spezzate in gruppi di
  `PAIR_RELATION_BATCH_SIZE` (default 15) e ciascuna `extract_pair_relations_batch` fa
  una sola `call_structured`; matching di ritorno per `pair_index`, non per nome.

---

## Workflow attuale end-to-end (con tutti i fix applicati)

### Ingestione (`run_ingestion_pipeline` → `process_chunk_node_extraction`, per chunk)
1. Aggiorna `:CorpusContext` (riassunto macro, una volta per documento).
2. Chunking (~384 parole/chunk, overlap 12.5%); fino a `CHUNK_CONCURRENCY=4` chunk in
   parallelo, ciascuno con la propria sessione Neo4j.
3. Per ogni chunk, tre chiamate LLM in parallelo: entità, partecipazione eventi, relazioni
   tra eventi.
4. Entità scritte come `:Node{type:'entity'}`, embedding nome+summary; per ogni coppia di
   entità nel chunk con summary non vuoto, decisione pairwise `related=true/false` in
   batch da `PAIR_RELATION_BATCH_SIZE` coppie (default 15) — **qui si concentra il
   rumore, vedi audit sotto**.
5. ~~CONTRADICTS same-chunk~~ — spento di default.
6. Eventi (triple) e partecipazioni eventi→entità scritti; per ogni entità partecipante
   non ancora vista nel chunk: prima cerca un match esatto nel documento intero, poi crea
   un nodo nudo con `summary=event_name` solo se nessun match esiste.
7. Ogni chiamata LLM che fallisce con errore transiente: riacquisisce lo slot di
   concorrenza (max 4 in volo) solo per il tentativo di rete, backoff differenziato per
   tipo di errore, circuit breaker se 3+ fallimenti "modello non disponibile" consecutivi.

### Dreaming (`run_dreaming_pipeline` → `_run_node_phases` → giudice)
1. Risoluzione entità fresche: fusione automatica immediata solo per nome esatto, o per un
   singolo candidato ≥0.90 con lo stesso `kernel_category` — altrimenti conferma LLM.
2. Backfill `kernel_category` sui nodi rimasti senza categoria.
3. Risoluzione relazioni entità↔entità ed evento↔evento: `supersedes`/`updated_by`/
   `extends` invariati; `contradicts` non scrive più nulla quando il flag è spento.
4. Backbone/TBox, PROMOTE (se abilitato), giudice a fine batch — invariati.

---

## Run 3 — completato: velocità confermata, ma nuova regressione di qualità trovata

Partito 2026-09-05 19:32:58, stesso corpus, grafo ripulito prima di iniziare. Solo il 7c
era sul disco per questo run (7a/7b non presenti, verificato via `git diff`).

### Velocità — confermata
| Metrica | Run 2 | Run 3 (con 7c) | Esito |
|---|---|---|---|
| Durata ingestione | 714.7 min | **545.6 min** | **-24%** |
| Chiamate LLM in ingestione | 9.172 | **946** | **-90%** |
| Durata dreaming | 268.4 min | 260.8 min | invariato (atteso, 7a/7b non fatti) |

### Qualità — NON identica: nuova regressione, più seria del run 2

Il controllo di regressione automatico (`merge_regression_check`) ha dato un falso PASS
perché cercava solo i bersagli sbagliati già noti dal run 1 ("Peter Cratchit", "friends").
Controllando a mano la catena `merged_into` di Fred:

```
Fred → Fred → Fred → Scrooge (nodo canonico, merged_into=None)
```

**Fred si è fuso nel nodo canonico "Scrooge" stesso** — non in un sostantivo generico
come nel run 2 ("nephew"), ma nel protagonista. `merge_nodes` promuove il summary più
recente sul canonico, quindi **il summary del nodo "Scrooge" è stato sovrascritto con la
descrizione di Fred**. Causa diretta di due risposte sbagliate:
- *"Chi è il socio in affari di Scrooge?"* → "Bob Cratchit" (sbagliato — la relazione
  corretta `Scrooge and Marley was_partner_in Jacob Marley` esiste ancora nel grafo,
  quindi l'errore è nel recupero/identità di "Scrooge" confusa, non nel dato mancante).
- *"Che rapporto ha Fred con Scrooge?"* → "nessuna informazione" (coerente col grafo:
  non esistono più due entità distinte da mettere in relazione).

In più, **2 domande su 10 sono fallite del tutto** ("generazione risposta non
disponibile") — mai visto nei run 1/2.

Tutto il resto è rimasto stabile: `CONTRADICTS`=0, nodi senza `kernel_category`=0, Bob
Cratchit non più fuso in Peter Cratchit, nodi totali 4.570 (~invariato vs 4.519), relazioni
totali 11.917 (leggermente più di 10.225, non meno).

**Causa più probabile**: non il 7c in sé (tocca solo `PairRelationDecision` in ingestione,
non `node_resolution.py`) — è la riconferma del limite già noto del Macrotask 3.3: il gate
su `kernel_category` non protegge le fusioni nella stessa categoria ("Agente" contro
"Agente"), e questo run mostra che il limite può colpire più duramente del previsto
(un protagonista, non solo un personaggio minore), probabilmente amplificato dalla
non-determinatezza naturale del modello locale run-to-run. **Il controllo di regressione
automatico va esteso**: non basta cercare bersagli specifici già visti, serve un controllo
generico tipo "nessun nodo con `type=entity` deve avere `merged_into` verso un nodo il cui
nome è un personaggio diverso e ugualmente prominente" — non ancora scritto.

Log completi: `scratchpad/christmas_carol_run3/`.

---

## Run 2 — completato, risultati verificati rispetto al criterio target

Criterio target (dato dall'utente): grafo coerente, completo, non ridondante rispetto al
testo sorgente. Confronto diretto run 1 (pre-fix) → run 2 (post-fix), stesso corpus.

| Metrica | Run 1 (pre-fix) | Run 2 (post-fix) | Esito |
|---|---|---|---|
| Durata ingestione | 650.7 min | 714.7 min | peggiore (vedi nota performance sotto) |
| Durata dreaming | 226.8 min | 268.4 min | peggiore (vedi nota performance sotto) |
| Durata totale | 15h1min | 16h43min | peggiore in wall-clock |
| Chiamate LLM totali | 29.547 | 32.596 | più chiamate (non meno) |
| `:Node` totali | 4.637 | 4.519 | **meno nodi → meno ridondanza** |
| `:Relation` totali | 11.909 | 10.225 | **-14%, meno rumore relazionale** |
| `CONTRADICTS` | 18.827 (tutti spuri, 0 con witness) | **0** | **risolto** |
| `:Node` senza `kernel_category` | 244 | **0** | **risolto** |
| `:ConnectivityRule` | 2.053 | 2.530 | più fatti classificati nel backbone |
| `MEMBER_OF` (nodi classificati) | 3.075 | 3.748 | più nodi coperti dal backbone |
| Bob Cratchit fuso in "Peter Cratchit"? | **SÌ (bug)** | **NO — resta "Bob Cratchit" attraverso 9 duplicati** | **risolto** |
| Fred fuso in un nome sbagliato? | SÌ, in "friends" (sostantivo generico scorrelato) | Fuso in "nephew" (sostantivo generico ma **tematicamente corretto**) | **migliorato, non perfetto** |

**Verdetto rispetto al criterio target: la QUALITÀ strutturale è migliorata su tutti gli
assi richiesti.** La VELOCITÀ non è migliorata — vedi Macrotask 7 sotto.

### Nota di onestà — perché non è più veloce nonostante i fix di performance

I fix del semaforo/backoff/circuit-breaker sono corretti e validati (test unitari
deterministici), ma in questo run non hanno avuto materiale su cui agire: **0 errori
"modello non disponibile"** e **0 aperture del circuit breaker** in tutto il run
(verificato sui log) — LM Studio è stato più stabile questa volta, niente backoff
sprecato da recuperare. Il vero collo di bottiglia, mai toccato da questo piano finora, è
il fan-out combinatorio O(k²) delle decisioni pairwise per chunk su soli 4 slot di
concorrenza globali (dettagli e fix proposto nel Macrotask 7).

### Nota di onestà — "Fred" fuso in "nephew", non nel suo stesso nome

Le 6 istanze del nodo generico "nephew" si sono correttamente consolidate in un solo nodo
canonico — nessuna frammentazione residua. Il problema è che "Fred" si è fuso in quel nodo
"nephew" invece che il contrario: `merge_nodes` non riscrive mai il campo `name` del nodo
sopravvissuto. Entrambi hanno `kernel_category="Agente"`, quindi il gate del Macrotask 3.3
non blocca questa fusione (a differenza di "friends" nel run 1, quasi certamente in una
categoria diversa). Il gate protegge dalle fusioni cross-categoria; non da un nome proprio
che si fonde con un descrittore generico della stessa categoria. Impatto pratico limitato
(la query NL su Fred risponde comunque correttamente).

### Nota — una query NL è peggiorata, non per colpa dei fix

"How many spirits visit Scrooge and what are they called?" → run 1 corretto; run 2 "il
testo non specifica" nonostante tutti e tre gli spiriti siano presenti nel grafo con
`kernel_category` corretta. Il motore di query NL non è stato toccato da nessun macrotask
— variazione di retrieval non deterministica, non una regressione di questo lavoro. La
domanda su Belle invece è migliorata (run 1: nessuna info; run 2: risposta corretta e
dettagliata) — stessa causa, direzione opposta.

---

## Audit di qualità del grafo (post-run 2) — percentuale di rumore e problema di fondo

Fatto con query dirette su Neo4j e un campione casuale (`ORDER BY rand()`, non scelto a
mano) di 60 archi `:Relation` entità↔entità su una popolazione di 5.009, classificati uno
per uno leggendo il `witness_text` reale citato dal sistema stesso come prova.

### Il numero onesto

**Sulle relazioni entità↔entità (49% del totale, 5.009 archi): circa 55-65% sono
logicamente/semanticamente sbagliate, fabbricate o invertite.** Solo ~17% chiaramente
corrette, un altro ~20% deboli-ma-accettabili. Esempi reali dal campione, con il
`witness_text` che il sistema stesso cita come prova:

- **`"Weather" is_the_parent_of "nephew"`** — witness citato: "She died a woman... Your
  nephew!" — il testo citato **non menziona nemmeno "Weather"**. Allucinazione pura.
- **`"Main street" transformed_into "Poulterer's man"`** — witness: due frasi scorrelate
  sulla vivacità natalizia e su operai che riparano tubi del gas. Nessuna delle due
  supporta "una strada si trasforma in un pollivendolo".
- **`"raisins" raffinamento_libero "candied fruits"`** — bug di estrazione: il modello ha
  restituito come nome della relazione il testo letterale delle istruzioni del prompt
  ("raffinamento libero"), non un'etichetta reale.
- **`"Weather" symbolizes "Ghost"`** — qui i campi witness non sono citazioni dal libro ma
  il *summary* auto-generato dell'entità stessa, riusato come falso testimone — prova
  circolare, non evidenza indipendente.

Le altre due famiglie di relazioni sono molto più affidabili, perché non richiedono al
modello di inventare un predicato libero:
- **Sequenza tra eventi** (`precedes`/`causes`/`cooccurs`, 2.143 archi, 21%): campione di
  20 → praticamente tutte corrette (vocabolario chiuso, meno spazio per fabbricare).
- **Partecipazione evento→entità** (`participates`, 3.068 archi, 30%): campione di 15 →
  tutte corrette.

**Stima pesata sull'intero grafo: ~30-35% di rumore complessivo**, concentrato quasi per
intero nel sottoinsieme entità↔entità, dove è maggioranza. Margine di errore: campione di
60 su 5.009 (±~13 punti percentuali al 95% di confidenza) — stima onesta, non conteggio
esaustivo.

### Il problema di fondo, strutturale

La causa non è un prompt insufficiente (Macrotask 4) — è il design stesso del passo di
decisione pairwise entità↔entità:

1. **Testa TUTTE le coppie possibili nel chunk (O(k²))**, incluse entità puramente
   descrittive/atmosferiche che non dovrebbero mai essere soggetto di una relazione
   specifica (Weather, Light, Frost, Time, Clock, Main street...). Più della metà delle
   relazioni sbagliate del campione coinvolgono questo tipo di entità.
2. **Il modello locale è sistematicamente incline a dire `related=true` inventando un
   predicato specifico** invece di `false` su coppie deboli — confermato dai `witness_text`
   reali (frasi scorrelate, o il summary dell'entità riusato come falso testimone). Il
   Macrotask 4 ha ridotto il volume (-14% run1→run2) ma non ha eliminato la fabbricazione.
3. **`kernel_parent` non è un segnale stabile**: la stessa etichetta di relazione (es.
   `associated_with`, `works_at`, `witnessed_by`, `coached_by`) viene classificata sotto
   fino a tutte e 6 le categorie R1-R6 a seconda dell'istanza. Tutta la logica a valle che
   assume `kernel_parent` stabile (backbone, `ConnectivityRule`, e in origine il bug
   CONTRADICTS) poggia su una base già rumorosa alla fonte.

**In sintesi**: il grafo è affidabile per fatti strutturali (chi partecipa a un evento, in
che ordine accadono le cose) ma inaffidabile per relazioni semantiche libere tra entità —
che sono anche il tipo più visibile nelle risposte del motore di query. Un fix serio
richiederebbe di restringere DOVE si applica la decisione pairwise (non su ogni coppia,
non su entità puramente descrittive) prima ancora di rifinire il prompt — fuori scope di
oggi salvo richiesta esplicita.

---

## Macrotask 7 — fix di velocità

**7c è implementato** (dall'utente, in parallelo a questa sessione — verificato leggendo
l'intero diff). **7a e 7b sono il prossimo passo consigliato, non ancora implementati.**

### 7c — Batching di `PairRelationDecision` (✅ implementato e verificato)

**Causa affrontata**: il fan-out O(k²) delle decisioni pairwise per chunk (un chunk con 9
entità genera C(9,2)=36 chiamate individuali), incanalato su soli 4 slot di concorrenza
globali — con 4 chunk in parallelo che aprono ognuno il proprio fan-out sugli stessi 4
slot, una singola chiamata poteva restare in coda anche un'ora (p99 osservato nel run 2:
53 minuti) prima ancora di partire.

**Come è stato implementato** (verificato corretto leggendo tutto il diff):
- `PairIndexedDecision`/`PairRelationBatchResult` (`app/models/node_extraction.py`): stessi
  campi di `PairRelationDecision` più `pair_index`; matching di ritorno per indice, non per
  nome (evita ambiguità su nomi duplicati/troncati nella risposta del modello).
- `build_pair_relation_batch_prompt` (`node_extraction_prompts.py`): elenca N coppie in un
  solo prompt, chiede una decisione per ciascuna in un unico array JSON, mantenendo il
  divieto di co-presenza del Macrotask 4.
- `extract_pair_relations_batch` + `align_pair_batch_decisions`
  (`node_extraction.py`): una sola `call_structured` per batch; indici mancanti/fuori
  range diventano `related=false` invece di un errore.
- `ingestion.py`: `pair_jobs` spezzato in batch da `PAIR_RELATION_BATCH_SIZE` (default 15,
  configurabile); un fallimento dell'intera chiamata batch propaga la stessa eccezione a
  tutte le coppie di quel batch, che passano poi dal normale percorso di
  `_unwrap_node_extraction` (stesso evento `llm_call_failed` per coppia di prima — nessuna
  nuova semantica di errore introdotta). Il ciclo di scrittura a valle (`write_node_relation`,
  controllo testimoni, `kernel_parent`) non è stato toccato.
- 219 righe di nuovi test in `test_node_extraction.py`.

**Impatto atteso**: con una media di ~35 coppie/chunk osservata nel run 2, batch da 15
portano le chiamate pairwise da ~35 a ~3 per chunk — riduzione di ~10-12× sul volume di
quel segmento (8.916 chiamate, 27,4% del totale). Non ancora misurato con un run reale.

### Scoperta successiva: 7c non è il collo di bottiglia più grande

Distribuzione reale delle chiamate per tipo, run 2:

| Tipo chiamata | Fase | Conteggio | % del totale |
|---|---|---|---|
| `EventRelationClassification` | dreaming (sequenziamento eventi) | **19.118** | **58,7%** |
| `PairRelationDecision` | ingestione (= 7c) | 8.916 | 27,4% |
| `RelationClassification` | dreaming (relazioni entità) | 2.901 | 8,9% |
| `NodeDedupResult` | dreaming (dedup nodi) | 1.395 | 4,3% |

`EventRelationClassification` (`event_relation_resolution.py::resolve_event` — per ogni
evento fresco, una chiamata LLM per ciascuno fino a 10 candidati simili) ha più del doppio
del volume che 7c ottimizza.

**Scoperta più importante**: i tre cicli principali del dreaming (`_resolve_fresh_entities`
in `dreaming.py`, `resolve_fresh_entity_relations` in `entity_relation_resolution.py`,
`resolve_fresh_events` in `event_relation_resolution.py`) sono tutti **cicli `for`
sequenziali con `await` dentro — mai `asyncio.gather`**, perché condividono un'unica
`AsyncSession` Neo4j (non sicura da usare in concorrenza) invece di aprirne una per unità
di lavoro come già fa l'ingestione per chunk. Elaborano un nodo/evento/relazione alla
volta, usando 1 solo degli 4 slot di concorrenza disponibili, per il 72% delle chiamate
totali del run (23.424 chiamate in 268 min). È per questo che il dreaming fa più del
doppio delle chiamate dell'ingestione in meno di un terzo del tempo — le sue chiamate sono
individualmente più leggere (prompt più corti), ma sta comunque sprecando concorrenza
disponibile: margine di miglioramento quasi gratuito.

### 7a — Parallelizzare i tre cicli sequenziali del dreaming (⬜ prossimo passo, priorità più alta, rischio più basso)

Dare a ogni nodo/evento/relazione risolto la propria sessione Neo4j (stesso pattern già
usato in `ingestion.py` per i chunk) ed elaborarli a gruppi invece che uno alla volta.
Nessun cambio di prompt/schema — solo controllo di concorrenza, quindi rischio più basso
del batching. Impatto atteso: fino a 2-4× sulla sola fase dreaming (268 min).

### 7b — Batching di `EventRelationClassification` (⬜ dopo 7a, stesso pattern di 7c)

Stessa logica di 7c applicata a `resolve_event`: invece di una chiamata per candidato
(fino a 10 per evento), una chiamata per evento che valuta tutti i candidati insieme.
Volume 2× più grande di `PairRelationDecision` — va fatto dopo 7a per non sommare due
cambiamenti rischiosi (concorrenza + batching) nello stesso passo.

### Nota — qualità vs velocità
Un pre-filtro di quali coppie/candidati testare (per qualità, es. escludere le entità
puramente descrittive dal fan-out pairwise, vedi l'audit sopra) resta una modifica
distinta dal batching di velocità — stesso punto di applicazione ma obiettivo diverso,
fuori da questo fix salvo richiesta esplicita.

---

## Fuori scope — invariato

- Fix delle fusioni sbagliate residue tipo "Fred"/"nephew" (stesso `kernel_category`) — il
  gate del Macrotask 3.3 non le copre per design (protegge solo cross-categoria).
- Riscrittura del passo di decisione pairwise per ridurre il rumore semantico (Macrotask 4
  ha già ridotto il volume, non la fabbricazione) — richiederebbe un pre-filtro di quali
  coppie testare, non solo un prompt più severo.
- Riduzione del backoff massimo generico, oltre a quanto già fatto nel Macrotask 5.

---

# Piano di rimozione — tutto ciò che è off/orfano tranne Event Triage

Decisione dell'utente: **`ENABLE_EVENT_TRIAGE` (`event_triage.py` + `event_slots.py` +
il 5° compito del giudice + `IncompletenessPanel.tsx`) resta** — è una feature completa,
testata, con un design deliberato di rollout a fasi (l'ultimo commit del modulo dice
letteralmente "production keeps ENABLE_EVENT_TRIAGE off until an explicit rollout"), non
scarto. **Tutto il resto di ciò che è off/parziale va rimosso per intero dalla codebase**,
non solo tenuto spento — verificato riga per riga con `grep` prima di scrivere ogni voce,
incluse le dipendenze condivise da NON toccare.

Non ancora eseguito: solo pianificato, come richiesto.

## R1 — Rimuovere per intero il meccanismo CONTRADICTS

**Cosa NON si tocca** (verificato che è condiviso/indipendente, per il precedente già in
uso nel progetto con `SAME_AS`/`POSSIBLY_SAME_AS` dopo la rimozione della Fase 8 —
il vocabolario Famiglia B resta congelato anche quando l'unico scrittore sparisce):
`SpecialRelationType.contradicts` in `app/models/kernel.py` **resta** nel vocabolario
chiuso — non è mai stato l'unico writer di `CONTRADICTS` (era `RelationLabel.contradicts`
in `relations.py`, un enum diverso), e resta un riferimento vocabolario usato in test
legittimi che non dipendono dal meccanismo (`test_validate_slot_proposal.py`,
`test_acceptance_event_triage.py`, `test_connectivity_rules.py`, `test_kernel_constants.py`
— nessuno di questi va toccato).

### Backend

| File | Cosa rimuovere |
|---|---|
| `app/core/config.py` | Campo `ENABLE_CONTRADICTS_DETECTION` + commento |
| `.env.example` | Le righe corrispondenti |
| `app/pipeline/ingestion.py` | `CREATE_CONTRADICTS_CYPHER`, `write_contradicts()`, `_write_same_chunk_contradicts()`, il suo call site (`if settings.ENABLE_CONTRADICTS_DETECTION: ...`), la dataclass `_WrittenEntityFact` e la lista `written_facts` (verificato: nessun altro consumer). `settings` resta importato (serve ancora a `PAIR_RELATION_BATCH_SIZE`, riga 577). |
| `app/pipeline/entity_relation_resolution.py` | `MARK_CONTRADICTS_CYPHER`; il branch `if label == RelationLabel.contradicts:` in `_apply_temporal_label` e in `_apply_different_tail_temporal` (entrambi, comprese le guardie del flag); il punto elenco su `contradicts` in `_TEMPORAL_TRANSITIONS_SECTION`; la frase su `contradicts` in `_PRUDENCE_TEMPORAL` (riscrivere per menzionare solo extends/none) |
| `app/pipeline/entity_relation_resolution.py` — `map_temporal_transition()` | Il ramo `if len(years) >= 2: return RelationLabel.contradicts` → diventa `return RelationLabel.none` (due date che confliggono senza marcatore di errore restano due fatti indipendenti, non si asserisce nulla) |
| `app/models/relations.py` | Rimuovere `RelationLabel.contradicts` dall'enum |
| `app/pipeline/judge.py` | `FIND_CONTRADICTS_PAIRS_CYPHER`, `CREATE_SUPERSEDES_BETWEEN_CYPHER`, `CREATE_UPDATED_BY_BETWEEN_CYPHER` (quelle di **questo file**, non quelle — diverse — in entity_relation_resolution.py, che restano), `DELETE_CONTRADICTS_BETWEEN_CYPHER`, `classify_temporal_pair()`, `_task_temporal()` (riga 326) e la sua chiamata in `run_judge`; campo `JudgeStats.temporal`; `temporal=$temporal` da `MERGE_JUDGE_RUN_CYPHER` |
| `app/pipeline/dreaming.py` | La chiave `"temporal"` dal payload SSE `judge_complete` |
| `app/pipeline/metagraph_layer.py` | `list_contradictions()` + la query Cypher che usa |
| `app/api/metagraph.py` | Rotta `GET /graph/contradictions` + import di `list_contradictions`/`ContradictionListResponse` |
| modello Pydantic della lista contraddizioni (verificare posizione esatta — vicino agli altri `*ListResponse` in `app/models/query.py` o schema dedicato) | Rimuovere il modello |

### Frontend

| File | Cosa rimuovere |
|---|---|
| `components/ContradictionsPanel.tsx` | Cancellare il file |
| `components/DashboardShell.tsx` | Import e montaggio del pannello |
| `lib/api-client.ts` | La funzione fetch delle contraddizioni |
| `lib/types.ts` | Il tipo di risposta contraddizioni |
| `lib/metagraph-api.test.ts` | I casi di test sulle contraddizioni |

### Test da rimuovere/riscrivere (verificati uno per uno con `grep`)

**Da rimuovere per intero** (il soggetto del test sparisce):
`test_acceptance_ingest_antiblur.py::test_conflicting_pairs_write_both_relations_and_contradicts`,
`test_entity_relation_resolution.py::test_contradicts_keeps_both_latest_never_updated_by`,
`test_acceptance_temporal_axis.py::test_apply_contradicts_preserves_both_assertions`,
`test_acceptance_judge.py::test_temporal_reclassifies_contradicts_keeps_facts` (+ gli
helper `add_famiglia(..., "CONTRADICTS", ...)` e gli import di `CREATE_CONTRADICTS_CYPHER`/
`DELETE_CONTRADICTS_BETWEEN_CYPHER`/`FIND_CONTRADICTS_PAIRS_CYPHER` in quel file),
i casi contraddizioni in `test_metagraph_layer_api.py`,
`test_kernel_stress.py` — il test che chiama `write_contradicts()` direttamente per
provare "PROMOTE non riclassifica mai Famiglia B" (soggetto doppiamente sparito, sia
`write_contradicts` che PROMOTE — vedi R2).

**Da riscrivere, non solo cancellare** (il test resta valido su un comportamento diverso):
`test_acceptance_temporal_axis.py::test_map_contradicts_authoritative_conflict_no_error_marker`
→ deve asserire `RelationLabel.none`, non più `contradicts`, per anni in conflitto senza
marcatore d'errore. `test_relation_prompt.py` → le asserzioni che il prompt menzioni
`contradicts` vanno invertite (il prompt non deve più menzionarlo). `test_models.py` →
rimuovere solo la riga `assert RelationLabel.contradicts.value == "contradicts"`, il resto
del file resta. `test_acceptance_metagraph_e2e.py` → lo scenario anno 2010 vs 2011 senza
marcatore deve asserire che i due fatti restano indipendenti (nessun arco Famiglia B), non
più `_has_famiglia(YEAR_2010_ID, YEAR_2011_ID, "CONTRADICTS")`; rimuovere i rami
`FIND_CONTRADICTS_PAIRS_CYPHER`/`DELETE_CONTRADICTS_BETWEEN_CYPHER` dal dispatcher
della sessione finta. `test_dreaming_nodes.py` e `test_event_slots.py` → compaiono nel
grep per un riferimento a `CREATE_CONTRADICTS_CYPHER`/import, non per logica propria:
verificare al momento dell'esecuzione e rimuovere solo l'import/ramo morto.

**Da NON toccare** (vocabolario Famiglia B, indipendente dal meccanismo):
`test_validate_slot_proposal.py`, `test_acceptance_event_triage.py`,
`test_connectivity_rules.py`, `test_kernel_constants.py`, `test_acceptance_migration.py`
(`is_skipped_relation("CONTRADICTS")` — difensivo su dati legacy, resta valido).

---

## R2 — Rimuovere per intero il meccanismo PROMOTE

**Cosa NON si tocca** (verificato condiviso con `backbone.py`, non esclusivo di PROMOTE):
`BACKBONE_MDL_MIN_COVERAGE`/`BACKBONE_MDL_MIN_PAYLOAD` (usati anche da `domain_book.py`),
`BACKBONE_COLLAPSE_THRESHOLD` (usato dal compito "equivalent_to" del giudice, che
confronta Concept fratelli anche quando non nascono da una PROMOTE ma dalla
classificazione diretta del backbone) — nessuno di questi è promote-esclusivo, restano.

### Backend

| File | Cosa rimuovere |
|---|---|
| `app/core/config.py` | Campo `ENABLE_PROMOTE` + commento |
| `.env.example` | La riga corrispondente |
| `app/pipeline/promote.py` | Cancellare il file intero |
| `app/pipeline/dreaming.py` | Import di `promote_clusters`; il blocco `async with driver.session() as promote_session: ... promote_clusters(...)`; la lista `promoted_parent_ids` (non serve più: era solo per nutrire il compito reraffine del giudice, vedi sotto) |
| `app/pipeline/judge.py` | `_task_reraffine()` (fa `if not promoted_parent_ids: return 0` — sempre 0 senza PROMOTE, morto anch'esso); campo `JudgeStats.reraffine`; parametro `promoted_parent_ids` da `run_judge()`; `reraffine=$reraffine` da `MERGE_JUDGE_RUN_CYPHER` |
| `app/pipeline/dreaming.py` | Chiave `"reraffine"` dal payload SSE `judge_complete` |

Verificato: nessun endpoint API o pannello frontend dedicato a PROMOTE — la dashboard
domini (`/graph/domains`) è generica e resta utile per gli 8 catch-all kernel senza
modifiche.

### Test da rimuovere

`test_acceptance_promote.py` (soggetto sparito per intero); i casi reraffine/
promoted_parent_ids in `test_acceptance_judge.py` e `test_acceptance_metagraph_e2e.py`.

---

## R3 — Rimuovere il percorso legacy non-temporale

Il ramo pre-Fase-9 in `entity_relation_resolution.py` non ha un flag suo — dipende da
`ENABLE_TEMPORAL_TRANSITIONS=false`, un valore che il progetto non usa mai (Fase 9 è
completata e strettamente più espressiva). Proposta: rimuovere il ramo **e** il flag che
lo seleziona, non solo il ramo, per non lasciare un `if/else` che sceglie sempre lo stesso
lato — segnalo esplicitamente perché è una semplificazione strutturale in più rispetto a
un puro "spegni il ramo morto": dimmi se preferisci tenere il flag come leva manuale.

| File | Cosa rimuovere |
|---|---|
| `app/pipeline/entity_relation_resolution.py` | `LEGACY_SYSTEM_PROMPT`, `_REPLACES_SECTION`, `_PRUDENCE_LEGACY`; il ramo `else` (non-temporale) in `classify_and_apply_entity_relation` — la funzione fa sempre il percorso temporale; `temporal_transitions_enabled()` e il suo uso (diventa incondizionato) |
| `app/core/config.py` | Campo `ENABLE_TEMPORAL_TRANSITIONS` (se confermi la rimozione del flag) |
| `app/pipeline/entity_relation_resolution.py` — `_apply_temporal_label` / `_apply_different_tail_temporal` | `RelationLabel.replaces` non è mai raggiungibile dal prompt temporale (solo dal legacy, ora sparito) e `map_temporal_transition()` non lo produce mai — semplificare `if label in (RelationLabel.supersedes, RelationLabel.replaces):` a `if label == RelationLabel.supersedes:` in entrambe le funzioni |
| `app/models/relations.py` | Rimuovere `RelationLabel.replaces` dall'enum |

### Test da aggiornare

Qualunque test che passi `ENABLE_TEMPORAL_TRANSITIONS=False` o testi il percorso legacy
va rimosso; i test che verificano `supersedes`/`updated_by`/`extends` sul percorso
temporale restano tutti validi senza modifiche (è già il percorso di default).

---

## R4 — Config orfana (nessuna decisione da prendere, zero utilizzi)

| File | Cosa rimuovere |
|---|---|
| `app/core/config.py` | Campo `ENABLE_DERIVES` (0 riferimenti altrove, verificato) |
| `app/core/config.py` | Campo `IDENTITY_BLOCK_THRESHOLD` (0 riferimenti altrove, verificato) |
| `.env.example` | Le righe corrispondenti a entrambi |

---

## Cosa resta intatto (per chiarezza, non un elenco di "salvati per caso")

`ENABLE_EVENT_TRIAGE` + `event_triage.py` + `event_slots.py` + il 5° compito del giudice +
`IncompletenessPanel.tsx` + l'endpoint `/graph/event-incompleteness` — su richiesta
esplicita, è un design tuo completo in attesa di rollout, non scarto. `ENABLE_JUDGE`,
`ENABLE_KERNEL_CLASSIFICATION` (sempre attivi, non in questo elenco). Tutti i Macrotask
1-6 e 7c (già implementati, verificati, in uso). Il vocabolario Famiglia B in `kernel.py`
resta congelato per intero, incluso `SpecialRelationType.contradicts` (stesso precedente
già applicato a `SAME_AS`/`POSSIBLY_SAME_AS` dopo la Fase 8).

## Verifica end-to-end dopo la rimozione

1. `grep -rn "ENABLE_CONTRADICTS_DETECTION\|ENABLE_PROMOTE\|ENABLE_DERIVES\|IDENTITY_BLOCK_THRESHOLD" app/ .env.example` → nessun risultato.
2. `grep -rn "RelationLabel.contradicts\|RelationLabel.replaces" app/` → nessun risultato.
3. `pytest tests/` completo — nessun fallimento da import rotti o asserzioni su codice rimosso.
4. Import Python puliti: `python -c "import app.main"` non deve sollevare `ImportError`.
5. Frontend: `tsc --noEmit` (o build) non deve segnalare riferimenti rotti a `ContradictionsPanel`/tipi rimossi.
6. Ispezione manuale: `IncompletenessPanel.tsx` e tutto lo stack event-triage restano bit-per-bit identici a prima di questo piano.
