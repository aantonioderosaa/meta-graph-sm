# Piano: pannello "Entità" con classificazione kernel (Event Graph live)

## Context

Nella pipeline event-graph live vengono già estratti, per ogni evento, soggetti (SOGG), oggetti (OGG) e attributi temporali (TEMPO), risolti in nodi `Menzione` e resi in UI come linee e nodi grigio chiaro (vedi vista "Tutto"). Questi partecipanti non sono oggi classificati semanticamente: l'obiettivo di questa feature è far valutare a un LLM ogni soggetto/oggetto estratto e posizionarlo in una delle categorie "kernel" concettualmente mutuate da `EntityKernelType` del Metagraph legacy (8 categorie E1–E8), aggiungendo una nona categoria "Temporale" che raccoglie deterministicamente tutto ciò che oggi porta il ruolo argomentale TEMPO. A livello frontend, va aggiunto un nuovo pannello "Entità" tra "Tutto" e "Ordine" che mostra questi 9 insiemi come gruppi scollegati tra loro ma espandibili (click per vedere i membri). Le relazioni tra entità sono esplicitamente fuori scope per questa iterazione.

## Decisioni vincolanti

- **Nessun tocco a `backend/app/models/kernel.py`.** Quel file appartiene al Metagraph dormiente e dichiara esplicitamente: *"No ninth horizontal primitive may be added without a presided human revision of the kernel"*. La separazione tra parte attiva (event-graph) e parte spenta (Metagraph) del backend è intenzionale e va preservata: questa feature resta interamente nella parte attiva. Verrà creato un vocabolario chiuso **nuovo**, scoped al solo event-graph, che rispecchia gli stessi 8 nomi/semantica di `EntityKernelType` (E1–E8) più il nuovo 9° valore "Temporale".
- **Classificazione:** un LLM valuta, uno per uno, tutti i soggetti (SOGG) e oggetti (OGG) già estratti/smistati dalla pipeline, assegnando una delle 8 categorie esistenti. Il 9° bucket "Temporale" **non** passa dall'LLM: raccoglie deterministicamente tutti i nodi che nella legenda del grafo portano l'etichetta di ruolo argomentale **TEMPO** (le attuali Menzioni grigio chiaro collegate agli eventi con arco "TEMPO").
- **Precedenza:** se una Menzione compare con ruolo TEMPO in almeno un arco argomentale, va nel bucket "Temporale" a prescindere da eventuali altri ruoli (SOGG/OGG) con cui compare altrove; solo le Menzioni mai viste con ruolo TEMPO vengono inviate all'LLM (solo se hanno almeno un ruolo SOGG o OGG).

## Assunzioni (da confermare/correggere in review, non bloccanti)

- Il bucket "Evento" viene popolato in modo deterministico dai nodi `:Evento` stessi (un Evento è per definizione categoria E4, zero costo LLM); l'LLM resta comunque libero di assegnare "Evento" anche a una Menzione SOGG/OGG se semanticamente pertinente.
- I ruoli argomentali OBL/LUOGO/MODO restano fuori scope per questa v1 (non entrano nel pannello Entità): solo SOGG, OGG (→ LLM) e TEMPO (→ regola) sono coperti.
- La categoria assegnata viene scritta come proprietà su `:Menzione` in Neo4j (non un nodo separato), così la vista può leggerla con una query semplice senza rieseguire la classificazione ad ogni fetch.

## Riferimenti chiave da riusare (non reinventare)

- Estrazione/ruoli: `ArgomentoGrezzo`/`ArgomentoRisolto` con `RuoloArgomentale = Literal["SOGG","OGG","OBL","TEMPO","LUOGO","MODO"]` — `backend/app/models/event_graph.py:17,97-104,255-311`. Risoluzione in `MenzioneRisolta` — `backend/app/pipeline/event_graph/segmentation.py::_resolve_event` (L109-160).
- Filtro ruolo TEMPO già esistente, pattern da riusare — `backend/app/pipeline/event_graph/ancore_estrazione.py::_forme_tempo` (L685-708).
- Styling grigio attuale (da mantenere per la vista "Tutto", da sostituire per-categoria nella vista "Entità") — `frontend/lib/event-graph/encoding.ts:21-29` (`MENZIONE_COLOR`, `ARGOMENTALE_COLOR`) e set `ARGOMENTALI` (L50-57).
- Pattern "gruppo compound + focus/espandi via stato", già implementato per la vista Ordine — `frontend/lib/event-graph/layout-ordine.ts::filterOrdineElements` (L61) + `EventGraphShell.tsx` stato `focusedZonaId` (L130-133, useMemo L135-141). Da mirror-are 1:1 per "Entità" invece di reinventare un accordion (non esiste alcun componente Accordion/Collapsible nel repo).
- Endpoint/dispatch vista — `backend/app/api/event_graph.py:324-348` (`Literal["tutto","ordine","temporale","relazioni"]`) e funzioni in `backend/app/pipeline/event_graph/catalog.py` (`grafo()`, `grafo_livello1()`, `grafo_livello2()`, `grafo_livello3()`, dict `_viste()` L127-178).
- Pattern step-documento LLM da mirror-are (naming, orchestrazione in `pipeline.py`, persistenza dedicata) — `livello_relazioni.py`/`estrai_livello_relazioni()`, `livello_temporale.py`, e relative `persisti_livello_*()` in `persistence.py`.
- Vocabolario/semantica delle 8 categorie da cui mutuare le descrizioni per il prompt LLM (senza importare il modulo) — `backend/app/models/kernel.py:35-51` (`EntityKernelType`, E1–E8) e `backend/app/pipeline/domain_book.py:49-210` (`CATEGORY_CARDS`, `criterio_appartenenza` per categoria).
- Palette colori per le 8 categorie, da cui ispirarsi (senza importare, per non ricoupling con la parte dormiente) — `frontend/lib/graph-encoding.ts` (`KERNEL_CATEGORY_COLORS`, L30-39, `colorByKernelCategory` L43-49).

## Macro-task 0 — Vocabolario chiuso locale "EntitaKernelCategoria"

- [ ] Definire un nuovo `Enum(str, Enum)` a 9 valori in `backend/app/models/event_graph.py` (Agente, OggettoFisico, Luogo, Evento, EntitaTemporale, EntitaInformativa, CostruttoSociale, EntitaAstratta, Temporale), con docstring che dichiara esplicitamente "vocabolario scoped al solo event-graph live, non è `EntityKernelType`".
- [ ] Aggiungere modelli `EntitaKernelClassificata` (`menzione_id`, `categoria`, `confidenza` opzionale) e container `LivelloEntitaResult` (`classificazioni: list`).

**Acceptance criteria**
- Il modulo `app.models.kernel` non viene importato da nessun file sotto `app/pipeline/event_graph/**` o `app/models/event_graph.py`.
- `len(EntitaKernelCategoria) == 9`.
- Test unitario che verifica i 9 valori esatti.

## Macro-task 1 — Step pipeline "Livello entità" (classificazione)

- [ ] Nuovo modulo `backend/app/pipeline/event_graph/livello_entita.py` con `estrai_livello_entita(eventi, menzioni)`.
- [ ] Raccogliere, a livello documento (dopo la riconciliazione coref di Fase B, così le Menzioni sono canoniche), l'insieme di Menzioni distinte per ruolo: quelle con ≥1 arco TEMPO → categoria "Temporale" assegnata direttamente (nessuna chiamata LLM); quelle senza TEMPO ma con ≥1 arco SOGG/OGG → candidate per l'LLM.
- [ ] Costruire il prompt con le 8 categorie ammesse (Agente…EntitaAstratta, "Temporale" esclusa dallo schema di output), riusando come riferimento testuale i `criterio_appartenenza` di `domain_book.py` (testo riscritto localmente, nessun import diretto); riusare il client LLM esistente (`backend/app/pipeline/event_graph/infra/llm.py`) con lo stesso pattern di retry/timeout degli altri step documento.
- [ ] Assegnare deterministicamente categoria "Evento" a ogni nodo `:Evento` (zero costo LLM).
- [ ] `persisti_livello_entita()` in `persistence.py` — `SET m.kernel_category = $categoria` per Menzione, batch singolo per documento.
- [ ] Wiring in `pipeline.py`, eseguito dopo Fase B (coref eventi/persist), con gestione errori non bloccante (come gli altri step documento) ed eventuale evento SSE dedicato (es. `entita_done`) coerente con la convenzione esistente (`macro_done`, `reconcile_done`, `pipeline_complete`).

**Acceptance criteria**
- Dato un documento di test (es. sole-e-vento), ogni Menzione risolta ha esattamente una `kernel_category` persistita.
- Nessuna Menzione con arco TEMPO riceve una categoria diversa da "Temporale".
- Il fallimento dello step non blocca `pipeline_complete`.
- Test unitario con LLM stub (mirror `test_event_graph_livello_temporale.py`) copre: SOGG/OGG classificati, TEMPO bypassato, Evento auto-assegnato.

## Macro-task 2 — Nuova vista API `entita`

- [ ] Estendere il `Literal` vista in `backend/app/api/event_graph.py:330` con `"entita"` e il dispatch a una nuova `grafo_entita()`.
- [ ] `grafo_entita()` in `catalog.py` — genera 9 nodi "gruppo" sintetici (uno per valore di `EntitaKernelCategoria`, `data.tipo="KernelCategoria"`, id stabile `kernel:<valore>`, presenti anche se vuoti) e i nodi Menzione con `kernel_category` non nulla come figli (`data.parent = kernel:<categoria>`), **nessun arco** (relazioni fuori scope).
- [ ] Aggiornare `_viste()` (L127-178) e la legenda/significato (`_SIGNIFICATO`, `GET /event-graph/catalog`) con la nuova vista.

**Acceptance criteria**
- `GET /event-graph/graph?vista=entita&documento=<id>` risponde con esattamente 9 nodi parent (anche vuoti) + N nodi Menzione figli, 0 edges.
- Risposta stabile e deterministica per lo stesso documento.
- Test di integrazione/unit sul nuovo endpoint (mirror test esistenti per `grafo_livello1`).

## Macro-task 3 — Frontend: tab "Entità" e pannello a gruppi espandibili

- [ ] Estendere `GraphFilters["vista"]` (`frontend/lib/event-graph/types.ts:131-136`) con `"entita"`; aggiungere eventuale nuovo valore di `EventGraphNodeTipo` (`types.ts:7-13`) per `"KernelCategoria"`.
- [ ] In `EventGraphShell.tsx` inserire `{ id: "entita", label: "Entità" }` in `VISTA_BUTTONS` **tra "Tutto" e "Ordine"** (L39-44); aggiungere voce in `LAYOUT_BY_VISTA` (L46-51); aggiungere stato `focusedCategoriaId` (mirror `focusedZonaId`) e resettarlo nello stesso `useEffect` che già resetta `focusedZonaId`/`ancoraPath` al cambio vista (L130-133); aggiungere branch nel `displayElements` useMemo (L135-141).
- [ ] Nuovo `frontend/lib/event-graph/layout-entita.ts` con `filterEntitaElements(elements, focusedCategoriaId)` — mirror 1:1 di `filterOrdineElements` (`layout-ordine.ts:61`): senza focus mostra solo i 9 gruppi (collassati), con focus mostra il gruppo cliccato + le sue Menzioni figlie. Click su un gruppo → `setFocusedCategoriaId`, mirror dell'handler già usato per le Zona.
- [ ] Styling — in `frontend/lib/event-graph/encoding.ts` aggiungere una palette locale per le 9 categorie (ispirata a `KERNEL_CATEGORY_COLORS` di `frontend/lib/graph-encoding.ts`, ma dichiarata localmente, nessun import cross-modulo) e un branch `encodeNode` per `tipo === "KernelCategoria"` (nodo pieno, forma distinta) + colorazione delle Menzioni figlie per categoria quando `vista === "entita"` (restano grigie nelle altre viste).
- [ ] Estendere `EventLegend.tsx`/`legend.ts` con le nuove voci (9 categorie + relativi colori).

**Acceptance criteria**
- La vista "Entità" appare come terzo pulsante nell'ordine Tutto → Entità → Ordine → Temporale → Relazioni.
- A vista aperta senza selezione si vedono 9 box scollegati (anche quelli vuoti, con conteggio membri a 0).
- Click su un box mostra le Menzioni contenute con colore per-categoria.
- Nessun arco è mai renderizzato in questa vista.
- Cambio vista resetta correttamente il focus (nessuno stato residuo da Ordine/Temporale).

## Macro-task 4 — Test e verifica end-to-end

- [ ] Unit test backend per `livello_entita.py` (LLM stub) — nessun costo/chiamata reale, no Docker, mirror suite esistente.
- [ ] Unit test frontend per `filterEntitaElements` (mirror `layout-ordine.test.ts`).
- [ ] Verifica manuale E2E — ingest corpus sole-e-vento, apertura tab "Entità", conferma visiva dei 9 gruppi, espansione di almeno un gruppo con Menzioni coerenti (es. "Temporale" contiene solo le espressioni temporali già viste come archi TEMPO in vista "Tutto").

**Acceptance criteria**
- `pytest -q tests/test_event_graph_*.py` (incluso il nuovo test) verde.
- `npm test` verde.
- Verifica manuale documentata (screenshot o nota) che i 9 gruppi combaciano con l'aspettativa (8 categorie E1–E8 + Temporale, scollegati, espandibili).
