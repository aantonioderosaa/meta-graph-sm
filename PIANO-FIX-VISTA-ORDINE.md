# Fix vista ordine — archi blu mancanti (RISOLTO)

Stato: **risolto e verificato live.** 2026-09-10. La dorsale di esposizione nasce in
ingestione e viene persistita; la vista ordine è passata da 7 eventi isolati a 0.
La diagnosi qui sotto è conservata come storico.

## Sintomo

Nella vista `ordine` di `sole-e-vento`, eventi senza nemmeno un arco:

| zona (ordinale) | id | eventi | isolati |
| --- | --- | --- | --- |
| 0 | `57c108c8eee0…` | 3 | 0 |
| 1 | `75925eef9276…` | 4 | 1 |
| 2 | `a86c9908d82d…` | 5 | 0 |
| **3** (la "4ª zona") | `8e3e2b7acffa…` | **6** | **6** |
| 4 | `c7a4b9a65e7f…` | 5 | 0 |

## Causa (tre comportamenti che si sommano)

1. `sentence_pair_linking.collega_adiacenti` ha etichettato **tutti e 5** i confini
   della zona 3 come `PRECEDE` invece di `SEQUENZA`: 0→1 (segnale "Poi", conf 0,95),
   1→2 (0,9), 2→3 (0,9), 3→4 (0,85), 4→5 (0,9). Sul testo l'etichetta è difendibile:
   `SEQUENZA` e `PRECEDE` dicono la stessa cosa e nulla arbitra fra i due.
2. `chiusura_temporale._collegato_ordine_menzione` — la rete di sicurezza che
   aggiunge un `COLLEGATO` "ordine_menzione" fra teste consecutive — **salta** la
   coppia se esiste già un `PRECEDE` (`_has_event_event(..., "PRECEDE", "COLLEGATO")`).
3. `catalog._L1_ORDER_EDGES_CYPHER` accetta solo `type(r) IN ['SEQUENZA','COLLEGATO']`
   con `a.chunk_id = b.chunk_id`.

Risultato: qualunque confine etichettato `PRECEDE` / `CAUSA` / `CONTRASTO` / `SCOPO`
non produce **nessun** arco nella vista ordine, e il fallback è disattivato proprio nel
caso più frequente. Il livello 1 dipende da una scelta lessicale dell'LLM invece che
dall'ordine di esposizione, che è deterministico e già noto (`posizione_chunk`).

## Difetti collaterali trovati

- Il filtro `a.chunk_id = b.chunk_id` esclude anche i **4 archi ponte** `SEQUENZA` di
  `pipeline.collega_dorsale_zone`, cioè la "ferrovia narrativa" fra zone. A livello
  evento la vista ordine non mostra i raccordi, solo i `SUCCESSIONE_ZONA` fra hub.
- **Tutti e 13** gli archi `CAUSA` del grafo vengono da `livello_relazioni.estrai`
  (livello 3), nessuno dal micro. Il livello 3 riusa i nomi di relazione del livello 1
  e quindi **salta** `event_edges.tipo_dopo_anti_causa_inventata`, che declassa
  `CAUSA`→`SEQUENZA` senza connettivo causale. Nella zona 3 il livello 3 ha creato una
  catena `CAUSA` 1→2→3→4→5 senza un solo "perché".

## Isolati per vista (misurato, eventi senza archi evento-evento)

| vista | eventi | isolati |
| --- | --- | --- |
| `tutto` | 23 | 0 |
| `ordine` | 23 | 7 |
| `temporale` | 23 | **10** |
| `relazioni` | 23 | 0 |

## Dorsale di esposizione (coppie consecutive per `posizione_chunk`)

**11 coppie su 18 coperte da `SEQUENZA`, 7 mancanti.** Con i 4 ponti cross-zona di
`collega_dorsale_zone`, la dorsale completa è 22 archi: 15 esistono, 7 mancano.

| zona (ord) | eventi | coppie | mancanti | dettaglio |
| --- | --- | --- | --- | --- |
| 0 | 3 | 2 | 1 | 1→2 ha COLLEGATO, CONTRASTO |
| 1 | 4 | 3 | 1 | 0→1 ha CONTENUTO, PRECEDE |
| 2 | 5 | 4 | 0 | — |
| 3 | 6 | 5 | **5** | tutte: PRECEDE + CAUSA/LIMITE/CONTEMPORANEO |
| 4 | 5 | 4 | 0 | — |

Buco separato del livello 2: l'evento `50eee594f1` ("Alla fine il Vento dovette
arrendersi") non appartiene a nessun `ClusterTemporale` — 22 `APPARTIENE_A` per 23
eventi. In quella vista è orfano due volte: nessun arco e nessun box contenitore.

## Direzione scelta (2026-09-10)

Opzione 1, con un requisito in più dell'utente: **la dorsale blu deve esistere "di
base"**, cioè essere **persistita all'ingestione** come struttura deterministica (un
arco per ogni coppia consecutiva, da `posizione_chunk`, senza chiamate LLM), non
sintetizzata dentro la query del livello 1. Così sta nel grafo salvato e compare in
ogni vista — in particolare nel livello 2, dove oggi mancano 10 eventi su 23.

Le due decisioni in sospeso sono state **chiuse così** (utente, 2026-09-10):

- **Tipo di relazione**: si riusa `SEQUENZA`. Resta blu senza toccare il frontend
  (`encoding.ts` mappa già `SEQUENZA` → `#1D4ED8`); la provenienza si distingue da
  `regola` / `base`, non dal nome del tipo.
- **Archi paralleli in `tutto`**: si tengono tutti. La dorsale è marcata
  `base: 'esposizione'` e si affianca a `PRECEDE` / `CAUSA` sulla stessa coppia:
  ordine di esposizione e relazione semantica sono asserzioni diverse.

## Implementazione (2026-09-10)

- `pipeline.collega_dorsale_eventi(sotto, zone)` (nuova, `pipeline.py`): per ogni zona
  ordina gli eventi vivi (`fuso_in` vuoto) per `posizione_chunk` (fallback
  `posizione_doc`, `offset_inizio`, `id`) e aggiunge un `SEQUENZA` fra ogni coppia
  consecutiva **solo se** quella coppia non ha già un `SEQUENZA` nella stessa
  direzione. Props: `regola: "pipeline.dorsale_esposizione"`, `base: "esposizione"`,
  `versione_regole`, `id = content_hash("SEQUENZA|da|a|esposizione")` (MERGE stabile
  fra run → idempotente anche in Neo4j). Zero chiamate LLM. Solo intra-zona: i
  raccordi fra zone restano di `collega_dorsale_zone`, che non è stata toccata.
- Chiamata in `run_event_graph_ingestion` subito prima di `collega_dorsale_zone`,
  quindi dopo l'espansione di tutte le zone; gli archi finiscono in Neo4j con la
  `persistence.persisti` di `_run_fase_b` (`SEQUENZA` è in `EVENT_EVENT_TIPI`).
- `catalog._L1_EVENTS_CYPHER` restituisce `posizione_chunk` e ordina per
  `z.ordinale, e.posizione_chunk`; `_l1_evento_element` lo espone nel `data` del nodo,
  così il layout preset del frontend impila i figli di una zona nell'ordine giusto.
  `_L1_ORDER_EDGES_CYPHER` invariato: con la dorsale persistita la vista si completa
  da sola.
- Test: nuovo `backend/tests/test_event_graph_dorsale_esposizione.py` (13 casi:
  catena, ordinamento, no-duplicati, parallelo a PRECEDE/CAUSA, no cross-zona,
  `fuso_in` esclusi, zona singola/vuota, idempotenza, persistibilità, aggancio alla
  pipeline) + copertura di `posizione_chunk` in `test_event_graph_m19.py`.

## Numeri live (`sole-e-vento`, wipe + re-ingestione completa)

| misura | prima | dopo |
| --- | --- | --- |
| vista `ordine`: eventi isolati | 7 / 23 | **0 / 23** |
| vista `ordine`: archi | 27 | **34** (18 `SEQUENZA` + 12 `COLLEGATO` + 4 `SUCCESSIONE_ZONA`) |
| coppie consecutive intra-zona coperte | 11 / 18 | **18 / 18** |
| `SEQUENZA` totali (vista `tutto`) | 15 | 22 (18 intra-zona + 4 ponti) |
| vista `tutto`: nodi / archi / isolati | 40 / 122 / 0 | 40 / 130 / **0** |
| `SEQUENZA {regola: 'pipeline.dorsale_esposizione'}` in Neo4j | 0 | **8**, tutti con `id` valorizzato e `base='esposizione'`, 0 cross-zona |

Nella nuova ingestione il classificatore di coppie ha etichettato `SEQUENZA` 10 dei 18
confini, quindi la dorsale ne ha aggiunti 8 (zona 3: tutti e 5, come nella diagnosi).

Viste `temporale` e `relazioni`: struttura invariata (temporale resta `PRECEDE` +
`CONTEMPORANEO`, relazioni resta senza isolati). Gli isolati di `temporale` sono
passati da 10 a 11 e i `PRECEDE` da 10 a 9 per **variabilità dell'LLM fra le due
ingestioni** (nella stessa run cambiano anche `CAUSA` 13→12, `CONTRASTO` 4→5,
`CONCESSIONE` 0→1, `CONTENUTO` 3→4): nessun percorso che produce `PRECEDE`
(`chiusura_temporale`, `temporal_placement._scrivi_precede`, `introdurrebbe_ciclo`)
guarda gli archi `SEQUENZA`. Il buco del livello 2 resta aperto e fuori da questo fix.

## Domanda aperta — chiusa: scelta l'opzione 1, in ingestione

**Come sistemare la vista ordine?** (opzioni non esclusive)

1. **Livello 1 deterministico** *(scelta e implementata, ma persistita
   all'ingestione anziché sintetizzata nella query)* — catena fra eventi consecutivi
   per `posizione_chunk`, senza dipendere dall'etichetta LLM.
   → è l'unica che ridà archi **blu** (`SEQUENZA`) nella zona 3.
2. **Allargare la query** — includere anche `PRECEDE` e gli altri tipi evento-evento
   intra-zona come spina dorsale.
   → connettività completa, ma colore teal (famiglia temporale), non blu; trascina
   dentro anche le coppie non adiacenti, che non sono "ordine".
3. **Riparare la rete di sicurezza** — `COLLEGATO` "ordine_menzione" sempre, anche
   quando esiste già un `PRECEDE`.
   → connettività completa, ma colore quasi bianco (`#E2E8F0`, spessore 1): di fatto
   invisibile.
4. **Includere i ponti cross-zona** di `collega_dorsale_zone` nella vista ordine.
   → non tocca l'isolamento interno di una zona.
5. **Applicare la guardia anti-`CAUSA`-inventata anche al livello 3.**
   → questione di qualità semantica, non aggiunge archi d'ordine.
