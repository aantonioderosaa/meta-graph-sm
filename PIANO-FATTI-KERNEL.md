# Piano: distinzione Fatti/Eventi, rinomina `:Evento`→`:Fatto`, 10° bucket kernel, summary multi-evento per riferimento

## Context

Il pannello "Entità" (vedi `PIANO-VISTA-ENTITA-KERNEL.md`, già implementato) oggi ha 9 bucket kernel: 8 categorie LLM (Agente…EntitaAstratta) + "Temporale" (regola). Il bucket "Evento" (una delle 8) è oggi popolato automaticamente da tutti i nodi `:Evento` del documento — questo confonde due concetti diversi:

- **Fatto** (nuovo): il nodo grafo che oggi si chiama `:Evento` — la frase/unità estratta da un chunk e inserita nel grafo. È un record strutturale, non una categoria semantica.
- **Evento** (kernel): categoria semantica dell'LLM per **menzioni** (soggetti/oggetti) che si riferiscono concettualmente a un accadimento (es. "la partita", "l'esibizione" come oggetto grammaticale di un altro fatto) — resta una delle 8 categorie LLM esistenti, **non** contiene più i nodi-fatto.

Decisione esplicita dell'utente: rinominare **ovunque** il label Neo4j/tipo esposto `:Evento` → `:Fatto` nel sistema live (non un'aggiunta di label, una rinomina reale), lasciando "Evento" libero come categoria kernel. Il grafo eventi "strutturale" (Ordine/Temporale/Relazioni, SEQUENZA/CAUSA/ancore/backbone) resta **comportamentalmente identico** — cambia solo il nome del nodo su cui opera, non la logica.

In aggiunta: nuovo **10° bucket "Fatti"** nel pannello Entità (tutti e soli i nodi `:Fatto` del documento); garanzia di completezza (nessun nodo estratto duplicato o perso tra i bucket); ogni nodo membro (tranne i fatti) porta un `summary` **per ciascun fatto a cui è collegato** (lista, non stringa concatenata — un soggetto/oggetto può comparire in più fatti), navigabile con frecce nel pannello laterale destro (`ElementInspector`) — tutto per riferimento, un solo nodo Neo4j/Cytoscape per entità, mai copie.

## Decisioni vincolanti (confermate con l'utente)

- Rinomina **reale e pervasiva** del label Neo4j `:Evento` → `:Fatto` e della stringa `tipo` esposta da API/frontend, non un label aggiuntivo.
- "Evento" nel kernel **non** contiene più i fatti: resta raggiungibile solo via classificazione LLM di Menzioni SOGG/OGG semanticamente eventive (es. "partita", "esibizione").
- Nuovo bucket "Fatti" (10°): popolato deterministicamente (zero costo LLM) da tutti e soli i nodi `:Fatto` del documento — stesso meccanismo "zero-cost" già usato per "Evento" nell'implementazione precedente, solo ripuntato.
- Completezza: somma membri nei 10 bucket = `count(fatti) + count(soggetti distinti) + count(oggetti distinti) + count(attributi temporali distinti)` esatto, nessun duplicato (una Menzione con più ruoli conta una volta sola, con la precedenza TEMPO già stabilita).
- `summary` per nodo non-fatto = riferimento **per ogni fatto collegato** (lista `{fatto_id, summary}`), non un riassunto unico.
- Navigazione multi-fatto: frecce prev/next nel pannello destro esistente (`ElementInspector.tsx`), per ora lì e basta.
- Tutto per riferimento: un solo nodo per entità nel payload, mai duplicato per-fatto-collegato; le viste Ordine/Temporale/Relazioni/Tutto restano semanticamente invariate (solo il nome del nodo cambia).

## Assunzione di scoping (da confermare in review, non bloccante)

I nomi **Python interni** (`EventoRisolto`, `EventoGrezzo`, moduli/funzioni come `event_edges.py`, `event_coref.py`) **restano invariati** — sono dettagli implementativi invisibili all'utente (54+10 call-site, rinominarli non cambia nulla di osservabile e aumenta solo il rischio). Cambiano solo: (a) il **label Neo4j persistito** (`:Evento`→`:Fatto`), (b) la **stringa `tipo`** restituita dalle API e consumata dal frontend (`"Evento"`→`"Fatto"` per i nodi-fatto), (c) constraint/indici in `schema.cypher`. Se l'utente vuole anche la rinomina dei simboli Python, va detto esplicitamente: è un lavoro aggiuntivo separato, a rischio più alto, senza benefici visibili.

## Portata reale della rinomina (ricognita e verificata file per file)

Il label Cypher `:Evento` compare solo in 8 file sorgente backend (non centinaia): `catalog.py` (17), `persistence.py` (19 — tutti `MERGE`/`MATCH` meccanici, es. L264, L432, L457-458, L622, L1328, L1365, L1385-1386, L1408-1409, L1433, **L1643 già toccata nella feature precedente** `(m:Menzione OR m:Evento)` → `(m:Menzione OR m:Fatto)`, L1862, L1896, L2138-2139, L2291), `chains.py` (4), `event_coref.py` (2), `query_structured.py` (6), `temporal_placement.py` (6), `api/event_graph.py` (1), `models/event_graph.py` (2, solo commenti/docstring: `PredicatoNonFinito` "never an :Evento node", `EventoRisolto` "Resolved :Evento fields..." — cosmetico, nessuna logica), più `infra/schema.cypher` (1 constraint, 6 indici, 1 fulltext, tutti su `:Evento`).

La stringa `"Evento"` come valore di `tipo` è concentrata in `catalog.py`: 3 blocchi identici (`_l1_evento_element` L665-685, l'equivalente per livello 2 e livello 3) che filtrano righe con `tipo == "Evento"` e ricostruiscono `"tipo": "Evento"` sul nodo compound — pattern meccanico, stesso fix ×3. Inoltre `catalogo()` (la funzione legenda, non `_viste()`) ha una entry `{"id": "Evento", "label": "Evento", "shape": "pieno"}` nella lista `"nodes"` — va rinominata anche questa (alimenta `EventLegend.tsx`).

Frontend: `types.ts` (union `EventGraphNodeTipo`), `EventGraphPanel.tsx` (2: fallback default + stub sintetico per diffing), `legend.ts` (2), `encoding.ts` (logica `filled`/`hub`), `ElementInspector.tsx` (1: controllo su `labels` Neo4j grezzo).

**Test — elenco verificato, non stimato.** File che referenziano `:Evento`/`"Evento"` come **label/tipo del nodo live** (da aggiornare, 12 file):
`test_event_graph_ancore_a6_corpus.py`, `test_event_graph_ancore_accettazione.py`, `test_event_graph_livello2_vista.py`, `test_event_graph_m9.py`, `test_event_graph_m19.py`, `test_event_graph_ancore_persist.py`, `test_event_graph_livello_relazioni_persist.py`, `test_event_graph_livello_temporale_persist.py`, `test_event_graph_m1.py`, `test_event_graph_m11.py`, `test_event_graph_m14.py`, `test_event_graph_m_macro0.py`. Pattern: fixture `"tipo": "Evento"` / asserzioni `tipo == "Evento"` / asserzioni sulla query Cypher (`assert "MERGE (e:Evento" in blob`, `assert "MATCH (da:Evento" in blob`, ecc.) — stesso find&replace mirato in ciascuno.

**Esplicitamente esclusi (verificati, NON toccare):**
- 8 file del Metagraph legacy dormiente che usano `"Evento"` come valore di `EntityKernelType.Evento` (kernel.py, E1-E8) — concetto e sistema completamente diverso: `test_acceptance_event_triage.py`, `test_acceptance_fact_placement.py`, `test_backfill_kernel_category.py`, `test_domain_book.py`, `test_domain_dashboard.py`, `test_event_triage.py`, `test_validate_slot_proposal.py`, `test_kernel_constants.py`.
- `test_event_graph_kernel_category.py` (live, ma testa `EntitaKernelCategoria.Evento` — la categoria kernel, che **resta** "Evento" per decisione esplicita — zero modifiche).
- Tutti gli altri ~100 file `test_event_graph_*.py` che usano solo il costruttore Python `EventoRisolto(...)`/`EventoGrezzo(...)` come fixture: nessuna modifica, il nome di classe Python resta invariato (vedi assunzione di scoping).

## Macro-task 0 — Rinomina label Neo4j `:Evento` → `:Fatto` (pipeline live)

- [ ] `infra/schema.cypher`: rinominare label in tutti i constraint/indici/fulltext (`eg_evento_*` → `eg_fatto_*` per coerenza nei nomi, opzionale ma consigliato).
- [ ] `catalog.py`, `persistence.py`, `chains.py`, `event_coref.py`, `query_structured.py`, `temporal_placement.py`, `api/event_graph.py`, `models/event_graph.py`: sostituire ogni `:Evento` (Cypher) e `"Evento"` (stringa tipo/label) con `:Fatto`/`"Fatto"`, **eccetto** dove il valore rappresenta la categoria kernel (da isolare esplicitamente prima di sostituire in blocco — non è un find&replace cieco). Include i 3 blocchi `_l{1,2,3}_evento_element` in `catalog.py` e l'entry `{"id": "Evento", ...}` nella lista `"nodes"` di `catalogo()` (legenda).
- [ ] Script di migrazione idempotente per i dati già ingeriti in dev (mirror di `backend/scripts/backfill_kernel_category.py`): `MATCH (n:Evento) SET n:Fatto REMOVE n:Evento` (+ equivalente per proprietà che referenziano il nome label, se presenti).
- [ ] `EntityKernelType`/`kernel.py` (Metagraph dormiente): **non toccare**, fuori scope, nessuna dipendenza da event-graph.

**Acceptance criteria**
- `grep -rn ":Evento\b"` sotto `app/pipeline/event_graph/`, `app/api/event_graph.py`, `app/models/event_graph.py`, `infra/schema.cypher` → zero risultati.
- Ingest di un documento nuovo crea nodi `:Fatto` (non `:Evento`) in Neo4j; le viste Tutto/Ordine/Temporale/Relazioni restituiscono lo stesso identico comportamento/struttura di prima (solo il nome cambia) su un corpus di regressione (es. sole-e-vento).
- Dati dev pre-esistenti migrati con lo script, oppure esplicitamente documentati come da re-ingerire.

## Macro-task 1 — Frontend: rinomina tipo "Evento" → "Fatto"

- [ ] `types.ts`: `EventGraphNodeTipo` union, `"Evento"` → `"Fatto"`.
- [ ] `EventGraphPanel.tsx`: fallback default (`ele.data("tipo") ?? "Evento"`) e stub sintetico di rimozione (`tipo: "Evento"`) → `"Fatto"`.
- [ ] `encoding.ts`: logica `filled`/`hub` (oggi `tipo === "Evento" || ...`) → `tipo === "Fatto" || ...`.
- [ ] `legend.ts`: default tipo (`tipo: id || "Evento"`, `tipo: "Evento"`) → `"Fatto"`.
- [ ] `ElementInspector.tsx`: `state.data.labels.includes("Evento")` → `"Fatto"`.
- [ ] **Non toccare** i punti dove `"Evento"` rappresenta la *categoria kernel* (label del bucket nel pannello Entità, palette colori) — isolare esplicitamente questi casi.

**Acceptance criteria**
- `grep -rn '"Evento"'` sotto `frontend/lib/event-graph/`, `frontend/components/event-graph/` → solo occorrenze legate alla categoria kernel "Evento" (bucket LLM), zero legate al tipo-nodo.
- `npx tsc --noEmit` pulito; `npx vitest run` verde dopo l'aggiornamento dei test (macro-task 5).

## Macro-task 2 — Kernel: 10° categoria "Fatti" + correzione auto-classificazione

- [ ] `EntitaKernelCategoria` (event_graph.py): aggiungere 10° membro `Fatti = "Fatti"`.
- [ ] `livello_entita.py`: **rimuovere** il blocco `classificazioni_evento` attuale (che oggi assegna categoria "Evento" a ogni nodo fatto/ex-evento) e sostituirlo con un blocco equivalente che assegna categoria **"Fatti"** a ogni nodo `:Fatto` del documento (stesso meccanismo zero-costo, solo ripuntato). La categoria "Evento" resta raggiungibile **solo** dall'LLM per Menzioni SOGG/OGG.
- [ ] `persistence.py::persisti_livello_entita`: il match `(m:Menzione OR m:Evento)` diventa `(m:Menzione OR m:Fatto)`.
- [ ] `catalog.py::grafo_entita`: aggiungere il 10° gruppo sintetico `kernel:Fatti` (stesso pattern con `ordinale`); l'union-query che oggi legge `:Evento` con `kernel_category` diventa lettura di `:Fatto`.

**Acceptance criteria**
- `len(EntitaKernelCategoria) == 10`.
- Ingest di un documento di test: bucket "Fatti" = esattamente tutti i nodi `:Fatto` del documento; bucket "Evento" = 0, a meno che l'LLM classifichi legittimamente una Menzione SOGG/OGG come concettualmente eventiva (es. "partita").
- Nessun nodo `:Fatto` riceve mai `kernel_category = "Evento"`.

## Macro-task 3 — Completezza: copertura esatta fatti+soggetti+oggetti+tempo

- [ ] Verificare/estendere la logica esistente di dedup (una Menzione con ruoli multipli conta una volta, precedenza TEMPO già in vigore) per includere anche i Fatti nel conteggio totale.
- [ ] Aggiungere un test che, su un documento di fixture, verifichi: `sum(count su tutti i 10 bucket) == count(:Fatto) + count(Menzioni distinte con ruolo SOGG) + count(Menzioni distinte con ruolo OGG) + count(Menzioni distinte con ruolo TEMPO, non già contate come SOGG/OGG per la precedenza)`.

**Acceptance criteria**
- Test di completezza verde su almeno un documento con Menzioni condivise tra più ruoli/eventi (caso non banale, non solo 1:1).

## Macro-task 4 — `summary` per riferimento, navigazione multi-fatto nella dash

- [ ] `catalog.py::grafo_entita`: per ogni Menzione membro, oltre alla categoria, raccogliere (via `collect()` Cypher, un'unica query aggiuntiva o estensione della query membri) **tutti** i `:Fatto` collegati da un qualunque arco argomentale, come lista `eventi_collegati: [{fatto_id, summary}]` — `summary` sorgente da `fatto.lemma` (oggi l'unico campo testuale rappresentativo su `EventoRisolto`; non esiste un campo `riassunto`/`summary` dedicato sul fatto). Un solo nodo Menzione nel payload, la lista è quella che veicola i riferimenti multipli — nessuna copia del nodo per fatto collegato.
- [ ] `types.ts`: nuovo campo `eventi_collegati?: {fatto_id: string; summary: string}[] | null` su `EventGraphNodeData`.
- [ ] `ElementInspector.tsx`: quando il nodo selezionato ha `eventi_collegati.length > 1`, mostrare controlli prev/next (frecce) con indice "N di M" che scorrono la lista mostrando il summary del fatto corrente, stato locale al componente (reset alla nuova selezione); con `length <= 1` nessun controllo (mostra il summary singolo o nulla).
- [ ] I nodi `:Fatto` stessi **non** portano `summary` (sono il fatto, non un riferimento a un fatto) — restano come oggi (label = lemma).

**Acceptance criteria**
- Una Menzione collegata a N fatti compare come **un solo nodo** nel payload `vista=entita`, con `eventi_collegati` di lunghezza N.
- Nel pannello destro, selezionando quel nodo, le frecce scorrono i summary dei fatti collegati senza nuove fetch/richieste di rete (dati già nel payload) e senza duplicare il nodo nel grafo.
- Una Menzione collegata a 1 solo fatto non mostra controlli di navigazione.

## Macro-task 5 — Test e verifica end-to-end

- [ ] Aggiornare i 12 file di test backend verificati nella sezione "Portata reale della rinomina" (`test_event_graph_ancore_a6_corpus.py`, `test_event_graph_ancore_accettazione.py`, `test_event_graph_livello2_vista.py`, `test_event_graph_m9.py`, `test_event_graph_m19.py`, `test_event_graph_ancore_persist.py`, `test_event_graph_livello_relazioni_persist.py`, `test_event_graph_livello_temporale_persist.py`, `test_event_graph_m1.py`, `test_event_graph_m11.py`, `test_event_graph_m14.py`, `test_event_graph_m_macro0.py`). **Non toccare** `test_event_graph_kernel_category.py` (testa la categoria kernel, non il label) né gli 8 file del Metagraph legacy elencati sopra.
- [ ] Aggiornare i 10 test frontend verificati con `tipo: "Evento"` come fixture/asserzione: `api.test.ts`, `encoding.test.ts`, `inspector.test.ts`, `layout-entita.test.ts`, `layout-ordine.test.ts`, `layout-relazioni.test.ts`, `layout-temporale.test.ts`, `layout-zigzag.test.ts`, `legend.test.ts` (incluso l'entry `{id:"Evento",...}` della legenda), `live-graph.test.ts` — non toccare eventuali asserzioni sulla categoria kernel "Evento".
- [ ] Nuovi test: conteggio 10 categorie, popolazione bucket "Fatti", completezza (macro-task 3), `eventi_collegati` multi-fatto + navigazione inspector.
- [ ] Verifica manuale E2E: ingest documento con almeno un soggetto/oggetto condiviso da 2+ frasi, conferma in Neo4j che i nodi sono `:Fatto`, conferma nel pannello Entità 10 gruppi corretti, conferma navigazione a frecce nel pannello destro su un nodo multi-fatto.

**Acceptance criteria**
- `pytest -q tests/test_event_graph_*.py` (esclusi i file con `SyntaxError` pre-esistente non correlato) verde.
- `npx tsc --noEmit` e `npx vitest run` verdi.
- Verifica manuale documentata.
