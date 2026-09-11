# Piano — Livello temporale v2 (cluster annidati, granularità secondi→anni)

Stato: **chiuso.** 2026-09-10. MT1–MT10 eseguiti; E2E live `sole-e-vento` PASS.
Sostituisce il comportamento attuale del livello 2 (`vista=temporale`).

## Requisiti (dall'utente)

1. Gli eventi della scheda temporale sono divisi in **cluster temporali che il sistema
   è sicuro di poter mettere insieme**. Se non è sicuro, non inventa il gruppo.
2. Un cluster può **contenerne altri**: due cluster stanno dentro un terzo. Gerarchia,
   non lista piatta.
3. Dove la collocazione temporale è incerta, il modello **stima comunque** una
   posizione nel tempo a partire dalle altre informazioni disponibili, e la dichiara
   come stima.
4. La granularità va **dai secondi agli anni**.

Lettura operativa dei punti 1 e 3, che non sono in contrasto: **posizionare è sempre
obbligatorio** (eventualmente per inferenza, marcata `stimato=true` con una
confidenza), **raggruppare richiede confidenza** (sotto soglia l'evento resta una
foglia a sé, comunque posizionata, invece di essere infilata in un cluster inventato).

## Perché lo stato attuale non basta (misurato su `sole-e-vento`)

- L'asse orizzontale è ordinato **per hash**: nessuna delle 6 etichette contiene una
  data, quindi `layout-temporale.isDatedCluster` non riconosce nessun cluster come
  datato e tutti finiscono nel fallback `a.id.localeCompare(b.id)`. Risultato: la
  storia inizia all'estrema destra. `timelineRankEdges` poi impone quell'ordine a
  dagre con archi `PRECEDE` invisibili.
- I cluster sono **piatti** e etichettati con riassunti narrativi
  ("Prova del Vento: soffio violento e resistenza dell'uomo"), non con collocazioni
  temporali: `tipo_cluster` è `intervallo` per tutti e 6, senza `inizio`/`fine`.
- **11 eventi su 23** non hanno né `PRECEDE` né `CONTEMPORANEO`.
- Tutti i **12 `CONTEMPORANEO` sono intra-cluster**, cioè ripetono con una freccia
  quello che il box già afferma; **7 dei 9 `PRECEDE`** sono a loro volta intra-cluster,
  quindi affermano "prima" dentro un gruppo che significa "stesso momento". Solo
  **2 archi su 21** collegano cluster diversi.
- Su **5 coppie** convivono un `PRECEDE` e un `CONTEMPORANEO`, con lo stesso colore.
- `PRECEDE` e `CONTEMPORANEO` sono indistinguibili: stessa famiglia in `encoding.ts`,
  stesso `#0D9488`, stesso spessore, stessa punta di freccia — anche se
  `CONTEMPORANEO` è simmetrico per progetto (decisione D2).
- La granularità massima gestita è il **giorno**: `parseIsoDate` in
  `layout-temporale.ts` e `temporal_placement._expand_bounds` si fermano a `YYYY-MM-DD`.
- Il prompt manda **il testo integrale del documento in ogni finestra**: su un
  documento come `dataset/christmas-carol.txt` sono ~39.400 token contro i 32.000 di
  contesto caricato in LM Studio, quindi tutte le finestre fallirebbero in silenzio.

## Modello dati di arrivo

### Nodo `ClusterTemporale`

| proprietà | tipo | note |
| --- | --- | --- |
| `id` | str | `cluster_temporale_id(doc, ...)`, stabile fra run |
| `etichetta` | str | **breve** (≤ 40 char), leggibile: "24 dic, sera" |
| `descrizione` | str \| null | la prosa che oggi sta in `etichetta` |
| `granularita` | enum | `secondo\|minuto\|ora\|giorno\|settimana\|mese\|stagione\|anno\|decennio\|secolo` |
| `inizio` | str \| null | ISO 8601 a **precisione variabile**: `1843`, `1843-12`, `1843-12-24`, `1843-12-24T18`, `1843-12-24T18:30`, `1843-12-24T18:30:15` |
| `fine` | str \| null | idem, per gli intervalli |
| `chiave_ordine` | int | **calcolata dal backend**, monotona; è ciò su cui il frontend ordina |
| `stimato` | bool | `true` se `inizio` è inferito e non dichiarato dal testo |
| `confidenza` | float | 0–1 |
| `tipo` | enum | resta `data_esplicita\|intervallo\|relativo\|simbolico` |

### Relazioni

- `(padre:ClusterTemporale)-[:CONTIENE]->(figlio:ClusterTemporale)` — **foresta**: ogni
  cluster ha al massimo un padre, nessun ciclo, granularità del padre più grossa di
  quella del figlio.
- `(e:Evento)-[:APPARTIENE_A]->(c:ClusterTemporale)` — **solo al cluster foglia** più
  specifico; i cluster più grossi si ricavano risalendo `CONTIENE`. L'arco porta
  `confidenza` e `stimato`.

Nessuna cancellazione: come oggi, tutto append-only e best-effort (un fallimento del
livello 2 non deve mai interrompere l'ingestione — decisione D6).

## Macrotask

Uno per subagent, in ordine. Ogni MT chiude con i suoi test verdi e non rompe i
precedenti. Nessun commit git.

**MT1 — Modelli.** `backend/app/models/event_graph.py`: `GranularitaTemporale`,
`ClusterTemporaleProposto` esteso (`descrizione`, `granularita`, `inizio`, `fine`,
`stimato`, `confidenza`, `padre`), `SegnaleTemporaleEvento` esteso (`granularita`,
`stimato`, `confidenza`, `base`). Retrocompatibilità: i campi nuovi hanno default,
i test esistenti che costruiscono i modelli vecchi devono restare verdi.

**MT2 — Tempo a precisione variabile (funzioni pure).** Nuovo modulo
`backend/app/pipeline/event_graph/tempo_iso.py`: parsing/validazione ISO 8601 a
precisione variabile da anno a secondo, `chiave_ordine(inizio, granularita) -> int`
monotona, e `bounds(inizio, granularita) -> (int, int)` per la rete di Allen. Estendere
`temporal_placement._expand_bounds` a ore/minuti/secondi **senza** cambiarne il
comportamento su anno/mese/giorno. Test tabellari su tutte le granularità, ordinamenti
misti (`1843` vs `1843-12-24T18:30`), input malformati.

**MT3 — Prompt e estrazione.** `livello_temporale.py`: nuovo `SYSTEM_LIVELLO_TEMPORALE`
che chiede (a) per ogni evento una collocazione **sempre**, dichiarando `stimato` e
`confidenza` quando è inferita, con la granularità più fine di cui è sicuro; (b) cluster
con etichetta breve, `inizio`/`fine`/`granularita`, e `padre` per l'annidamento.
Sostituire l'input: **non** il testo integrale del documento ma i riassunti delle zone
coinvolte più gli span degli eventi della finestra (risolve anche il superamento di
contesto sui documenti lunghi). Gate di confidenza: sotto `SOGLIA_CLUSTER = 0.6`
l'appartenenza non viene emessa e l'evento resta foglia a sé, comunque posizionato.

**MT4 — Riconciliazione e gerarchia.** Fondere i cluster fra finestre per
`(inizio, granularita)` normalizzati invece che per etichetta identica (oggi
`_merge_results` confronta le etichette). Costruire la foresta: risolvere `padre`,
scartare cicli e padri con granularità non più grossa del figlio, garantire un solo
padre. Funzioni pure, test sui casi degeneri.

**MT5 — Persistenza e schema.** `persistence._merge_cluster_temporale` con le nuove
proprietà, nuovo `_merge_contiene`, `APPARTIENE_A` solo sulla foglia con
`confidenza`/`stimato`. `infra/schema.cypher`: indici su `ClusterTemporale.chiave_ordine`
e `ClusterTemporale.documento`. Non toccare i `CONTEMPORANEO` esistenti in questo MT.

**MT6 — Vista livello 2.** `catalog.grafo_livello2`: proiettare la gerarchia come
`data.parent` (compound Cytoscape), più `chiave_ordine`, `granularita`, `stimato`,
`confidenza`, `etichetta`, `descrizione`. Escludere dagli archi i `CONTEMPORANEO`
**intra-cluster** (ridondanti con il box) tenendo i cross-cluster. Ordinare le righe
per `chiave_ordine`.

**MT7 — Legenda e dettaglio.** `catalogo()`: aggiornare la vista `temporale` con
`CONTIENE` e il significato di `stimato`/`granularita`. `dettaglio_nodo` per
`ClusterTemporale` deve mostrare le nuove proprietà. Attenzione al test
`test_catalogo_viste_covers_levels_and_meanings`, che asserisce l'insieme esatto degli
archi della vista.

**MT8 — Layout frontend.** `frontend/lib/event-graph/layout-temporale.ts`: ordinare per
`chiave_ordine` fornita dal backend ed **eliminare** tutto il parsing di date lato
client (`parseIsoDate`, `extractDateKey`, `isDatedCluster`, `resolveDatedAnchor`).
Fallback per cluster senza collocazione: minima `posizione_doc` degli eventi contenuti
— **mai** l'id. Gestire la gerarchia: i rank edge invisibili vanno solo fra cluster
**fratelli dello stesso livello**, non fra un padre e un nipote.

**MT9 — Codifica visiva.** `frontend/lib/event-graph/encoding.ts`: `CONTEMPORANEO`
diventa distinguibile da `PRECEDE` — nessuna punta di freccia (è simmetrico), tratto
tratteggiato e colore proprio. Cluster `stimato` con bordo tratteggiato. Etichette dei
box: `etichetta` breve, la `descrizione` va nel tooltip.

**MT10 — E2E live.** Wipe + re-ingestione di `sole-e-vento` e verifica numerica.

## Criteri di accettazione (da misurare su `sole-e-vento`)

- Ordine dell'asse: il primo cluster a sinistra contiene l'evento con
  `posizione_doc` minima. Oggi è l'ultimo a destra.
- Almeno un livello di annidamento presente (`CONTIENE` > 0) e foresta valida:
  nessun ciclo, ogni cluster con ≤ 1 padre, granularità decrescente scendendo.
- Ogni evento ha una collocazione: `inizio` valorizzato sul cluster foglia oppure
  `stimato=true`. **Zero eventi senza posizione.**
- Nessun `CONTEMPORANEO` intra-cluster nella vista (oggi 12 su 12 lo sono).
- Nessuna coppia con `PRECEDE` e `CONTEMPORANEO` insieme (oggi 5).
- `granularita` valorizzata su tutti i cluster; `chiave_ordine` monotona e coerente
  con `inizio`.
- Le viste `tutto`, `ordine`, `relazioni` non cambiano.
- Nessun nuovo fallimento nei test; i 6 preesistenti in `test_event_graph_m4.py` e
  `test_event_graph_m_dedup.py` restano fuori scope.
