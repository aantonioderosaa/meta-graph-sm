# Piano di fix — Suite pytest, anomalia payload, Svuota KB, concorrenza ingestione

Stato di partenza: analisi dell'ultima ingestione live (`sole-e-vento`, job `8b93fbd5-5c29-4de1-9261-9b96963e2f67`, vedi `backend/_mt10_ingest_log.txt` e `backend/_mt10_measure.json`). Tutti i criteri strutturali (1-8 sul documento) erano PASS, ma la suite pytest completa e due comportamenti runtime (Svuota KB, ingestioni concorrenti) hanno problemi reali. Questo piano copre tutti e quattro, divisi in macrotask indipendenti dove possibile e con una dipendenza esplicita dove no.

Ogni macrotask è pensato per essere assegnabile a una persona diversa in parallelo, tranne dove indicato.

## Mappa delle dipendenze

```
MT1  Fix bug ereditarietà fattualità  ─────────────────┐
MT2  Quarantena test legacy Metagraph ─────────────────┤  indipendenti, mergeable subito
MT3  Fix doc_id nel payload di esempio ────────────────┘

MT4  Infrastruttura "job di ingestione attivo"  ──┬──> MT5  Guard concorrenza ingestione
                                                    └──> MT6  Fix Svuota KB (cancellazione totale)
```

MT1–MT3 non toccano codice condiviso con MT4–MT6 e possono partire subito, in parallelo, da persone diverse. MT5 e MT6 dipendono entrambi dal contratto (le funzioni) definito in MT4: se serve parallelizzare anche questi due, congelare prima l'interfaccia di MT4 (vedi sezione MT4) e farli partire su quella firma, allineando l'implementazione reale a fine MT4.

---

## MT1 — Fix bug ereditarietà fattualità (`factuality.py`)

**Dipendenze:** nessuna. **Effort stimato:** M.

**Problema.** 5 test falliscono in `backend/tests/test_event_graph_m4.py`:
`test_non_fattuale_signals_become_fuori_linea`, `test_completiva_non_fattivo_vs_fattivo_exception`, `test_inheritance_descends_and_never_climbs`, `test_span_reorder_resolves_completiva_via_indice_grezzo`, `test_factsheet_span_fallback_without_indice_grezzo`.

**Causa radice.** In `backend/app/pipeline/event_graph/factuality.py`, `_is_realized_assertion()` (righe 34-47) ritorna `True` per qualsiasi evento con tempo passato/imperfetto/trapassato/presente, `ruolo_se="nessuno"`, non negato e `segmentazione=None` — **senza considerare se l'evento è sintatticamente imbarcato** (cioè se `completiva_di` risolve a un padre reale). Questo controllo viene usato in due punti e in entrambi causa il bug:

1. `_local_fattualita()` (riga 61) lo valuta **prima** dei controlli su `modalizzato`, `finale`, `frase_tipo` (interrogativa/imperativa) e `completiva_di + classe_verbo_reggente == "non_fattivo"` — scavalcandoli tutti.
2. `_inherit_down()` (riga 133) lo usa come guardia (`not _is_realized_assertion(current)`) per decidere se un figlio può ereditare `NON_FATTUALE`/`IPOTETICO` dal padre: un figlio con `completiva_di` che risolve a un padre reale non eredita mai, perché viene considerato "già realizzato" a prescindere dalla subordinazione.

Il docstring del modulo chiarisce l'intento originale: l'immunità di `_is_realized_assertion` serve solo a proteggere una clausola principale reale da un `finale`/`completiva_di` **spurio** (rumore LLM, puntatore che non risolve a nessun padre — il caso già coperto e da non rompere: `test_dangling_completiva_is_skipped`). Il bug è che oggi l'immunità è incondizionata invece che limitata a quel caso.

**Fix proposto.**
1. In `_local_fattualita()`, spostare i controlli `modalizzato`, `frase_tipo in _NON_FATTUALE_FRASI` e `tempo == "futuro"` **prima** di `_is_realized_assertion`, lasciando che quest'ultima protegga solo `finale` e `completiva_di + non_fattivo`.
2. Restructurare `applica()` per calcolare `by_indice`/`parent_of` **prima** del primo giro di calcolo di `_local_fattualita` (oggi calcolati dopo), e passare al chiamante l'informazione "questo `completiva_di` risolve a un padre reale?". Negare l'immunità di `_is_realized_assertion` quando il puntatore risolve a un padre reale; mantenerla solo per puntatore assente/dangling.
3. Applicare la stessa condizione alla guardia in `_inherit_down()`.

**Criteri di accettazione.**
- Tutti i test in `test_event_graph_m4.py` verdi, incluso `test_dangling_completiva_is_skipped` e il ramo "fattivo" di `test_completiva_non_fattivo_vs_fattivo_exception` (non regredire i casi che oggi passano).
- Suite completa `pytest tests/test_event_graph_*.py tests/test_acceptance_event_graph_e2e.py` verde (per escludere effetti collaterali su altri moduli che chiamano `factuality.applica`).
- Nessuna modifica a `narrative_plane.py` a meno che l'investigazione non riveli una dipendenza diretta (verificarlo prima di escluderlo).

**Rischio.** `_is_realized_assertion` è probabilmente usata/copiata concettualmente altrove (verificare `narrative_plane.py`, che importa `factuality`); un cambio di ordine dei controlli può avere effetti a catena su `piano` (PRIMO_PIANO/FUORI_LINEA) — da ricontrollare con i test `assegna_piano`.

---

## MT2 — Quarantena test legacy Metagraph (`test_node_graph_api.py`)

**Dipendenze:** nessuna. **Effort stimato:** S.

**Problema.** 7 test falliscono con `404` (`test_get_entity_graph_returns_nvl_shape`, `test_get_event_graph_returns_nvl_shape`, `test_get_participation_graph_returns_nvl_shape`, `test_get_concept_overview_returns_nvl_shape`, `test_get_concept_neighbors_returns_nvl_shape`, `test_get_entity_graph_passes_include_concepts`, `test_get_event_graph_passes_include_concepts`).

**Causa radice.** Non è un bug: `backend/app/main.py:48` monta solo `event_graph_api.router`. Il router Metagraph (`app/api/node_graph.py`, rotte `/graph/*`) non è mai incluso nell'app live, come già documentato nel README ("Codice Metagraph dormiente... non montato"). Questi test istanziano `app.main.app` aspettandosi rotte che nel prodotto live non esistono. Non fanno parte della suite documentata nel README (`pytest tests/test_event_graph_*.py tests/test_acceptance_event_graph_e2e.py`) — emergono solo in un run "full pytest" senza filtro.

**Decisione da confermare con il team/product owner:** skip o cancellazione del file?
- **Skip (raccomandato):** `@pytest.mark.skip(reason="Metagraph /graph/* non montato in app.main; vedi README §Storico — Metagraph")` a livello di modulo. Coerente con come CI disattiva già i job Metagraph con `if: false`; il test resta pronto se il Metagraph verrà rimontato in futuro.
- **Cancellazione:** più pulito se il Metagraph è considerato morto in modo definitivo, ma perde la copertura futura.

**Criteri di accettazione.** Un run pytest full-repo non riporta più questi 7 falliti (skippati o rimossi), e la CI resta verde.

---

## MT3 — Fix doc_id nel payload di esempio

**Dipendenze:** nessuna. **Effort stimato:** XS.

**Problema.** `backend/_ingest_sole_vento.json` ha `"doc_id": "sole-vento"`, ma il README e `backend/_mt10_measure.json` (l'output della vera ultima ingestione) usano `"sole-e-vento"`. Se questo payload viene riusato per un run manuale, l'id non corrisponde a quello atteso dagli script E2E (`_e2e_assert.py`, `test_acceptance_event_graph_e2e.py`).

**Fix.** Correggere il campo a `"doc_id": "sole-e-vento"`.

**Criteri di accettazione.** `grep doc_id backend/_ingest_sole_vento.json` → `sole-e-vento`; un'ingestione manuale con questo payload produce un documento riconosciuto dagli script E2E esistenti.

---

## MT4 — Infrastruttura "job di ingestione attivo"

**Dipendenze:** nessuna (ma blocca MT5 e MT6 — dare priorità).
**Effort stimato:** M.

**Obiettivo.** Oggi non esiste alcun modo per sapere "c'è un'ingestione in corso?" né per fermarla. `POST /event-graph/documents` (`api/event_graph.py:90-102`) lancia `asyncio.create_task(...)` **senza tenerne un riferimento**, e l'event bus (`infra/bus.py`) traccia solo la history degli eventi SSE, non l'handle del task. Questo MT costruisce l'infrastruttura condivisa che MT5 e MT6 useranno.

**Contratto proposto (da rispettare per non bloccare MT5/MT6 in parallelo):**
- `infra/bus.py`: nuovo `_active_tasks: dict[str, asyncio.Task]`, popolato da chi crea il task di ingest.
- `has_running_job() -> str | None`: ritorna il `job_id` del job con `job_status(...) == "running"`, se esiste (riusa `elenca_job()`/`job_status()` già presenti).
- `cancel_job(job_id: str) -> None`: chiama `.cancel()` sul task tracciato, se presente; no-op se il job non è tracciato o già concluso.
- `register_running_task(job_id: str, task: asyncio.Task) -> None`: da chiamare in `api/event_graph.py` subito dopo `asyncio.create_task(...)`.

**Passi.**
1. Implementare le funzioni sopra in `infra/bus.py` con test unitari dedicati (job registrato → `has_running_job()` lo trova; job concluso → non lo trova più; `cancel_job` su task già concluso non solleva eccezioni).
2. Aggiornare `api/event_graph.py:90-102` per tenere il riferimento al task e registrarlo.
3. Assicurarsi che `reset_event_bus()` (già esistente) pulisca anche `_active_tasks`.

**Criteri di accettazione.** Nuovi unit test su `infra/bus.py` verdi; nessuna modifica di comportamento visibile finché MT5/MT6 non la usano (questo MT non cambia risposte HTTP).

---

## MT5 — Guard concorrenza su `POST /event-graph/documents`

**Dipendenze:** MT4. **Effort stimato:** M.

**Problema.** Nessun controllo impedisce due ingestioni concorrenti sullo stesso grafo Neo4j: due `POST /event-graph/documents` in parallelo scriverebbero entrambe su zone/eventi dello stesso documento, con interleaving imprevedibile (corse tra le fasi MACRO/MICRO dei due job).

**Fix.**
1. Backend: in `ingest_event_graph_document()`, prima di creare il task, chiamare `has_running_job()` (MT4). Se ritorna un `job_id`, rispondere `409 Conflict` con corpo `{"detail": "Ingestione già in corso", "job_id": "<attivo>"}` invece di avviare un nuovo job.
2. Frontend (`EventIngestPanel.tsx`): oggi il bottone "Ingerisci" è disabilitato solo durante la chiamata HTTP di submit (`busy`), non per tutta la durata della pipeline (`busy` torna `false` appena arriva il `job_id`, molto prima di `stage: done`). Tracciare lo stato del job via lo stream SSE già consumato altrove (`EventPipelineMonitor`) e tenere il bottone disabilitato finché non arriva `stage: done|failed`. Gestire la risposta 409 mostrando l'errore con il `job_id` già attivo.

**Decisione da confermare:** rifiutare (409, raccomandato — comportamento esplicito e semplice) o accodare la seconda richiesta? Accodare aggiunge complessità (coda, ordine, timeout) senza un bisogno di prodotto evidente: raccomando il rifiuto esplicito.

**Criteri di accettazione.**
- Test backend: due `POST /event-graph/documents` consecutive senza attendere il completamento → la seconda risponde `409`.
- Test frontend: submit di un secondo documento mentre `jobId` è impostato e lo stage non è `done`/`failed` → bottone disabilitato, non parte una seconda richiesta.
- Manuale: due tab browser, avviare due ingest quasi simultanee → una sola procede.

---

## MT6 — Fix "Svuota KB" (garantire cancellazione totale)

**Dipendenze:** MT4. **Effort stimato:** M.

**Problema.** `wipe_grafo()` (`persistence.py:2351`, `MATCH (n) DETACH DELETE n`) cancella davvero tutto **a livello Neo4j nell'istante in cui gira**. Il bug è che `DELETE /event-graph/graph` (`api/event_graph.py:119-132`) non controlla né ferma un'ingestione in corso: se un job è ancora attivo quando l'utente clicca "Elimina tutto", il task in background continua a scrivere Zone/Fatti/Menzioni per il resto della sua pipeline, ripopolando silenziosamente il grafo pochi secondi dopo lo svuotamento. In più, la prossima `publish()` di quel job richiama `register_job()` (`infra/bus.py:161`), che — non trovando più il job in `_history` dopo `reset_event_bus()` — lo **ri-registra come se fosse nuovo**, facendolo ricomparire nella lista job senza che l'utente abbia lanciato nulla.

C'è anche una lacuna minore nel frontend: `EventGraphShell.handleWiped()` (righe 199-214) resetta quasi tutto lo stato locale ma non `focusedCategoriaId` (stato della vista "entità" — resta a puntare a un nodo ormai cancellato dopo lo svuotamento).

**Decisione da confermare con il team/product owner:** cosa deve succedere se l'utente clicca "Svuota KB" mentre un'ingestione è in corso?
- **Cancellare il job e procedere comunque (raccomandato):** usare `cancel_job()` (MT4) prima di `wipe_grafo()`. Dato che si sta comunque cancellando tutto, eventuali scritture parziali del task cancellato sono irrilevanti — garantisce che "Elimina tutto" sia sempre vero, senza far attendere l'utente.
- **Bloccare lo svuotamento** finché il job non termina (risposta 409 simmetrica a MT5, con messaggio "attendi il completamento dell'ingestione"): più prevedibile ma meno comodo, l'utente deve aspettare anche se vuole solo annullare un'ingestione partita per errore.

**Fix proposto (assumendo l'opzione raccomandata).**
1. Backend: in `wipe_event_graph()`, prima di `wipe_grafo()`, chiamare `has_running_job()` (MT4); se c'è un job attivo, chiamare `cancel_job(job_id)` e attendere la cancellazione (gestire `asyncio.CancelledError` in `run_tracked_job`/`run_event_graph_ingestion` per un'uscita pulita, senza pubblicare un evento `failed` fuorviante). Poi procedere con `wipe_grafo()`, `reset_query_history()`, `reset_event_bus()` come oggi.
2. Frontend: aggiungere `setFocusedCategoriaId(null)` in `handleWiped` (`EventGraphShell.tsx`).

**Criteri di accettazione.**
- Test backend: avviare un job (mockato/lento), chiamare `DELETE /event-graph/graph` mentre è ancora in `running`, attendere il tempo che avrebbe impiegato il job a completare, verificare che il conteggio nodi in Neo4j resti a 0 (non ripopolato) e che `elenca_job()` non mostri il job ricomparso.
- Test frontend: dopo `onConfirmWipe()`, tutti gli stati locali di `EventGraphShell` risultano azzerati, incluso `focusedCategoriaId`.
- Manuale: avviare un'ingestione su un testo lungo, cliccare "Svuota KB" a metà pipeline, verificare che il grafo resti vuoto anche dopo il tempo in cui l'ingestione sarebbe normalmente terminata.

---

## Sequenza di merge consigliata

1. **Subito, in parallelo:** MT1, MT2, MT3 — tre PR indipendenti, ciascuna mergeable non appena verde.
2. **Poi:** MT4 da solo (foundation, PR piccola e a basso rischio perché non cambia comportamento osservabile).
3. **Dopo MT4:** MT5 e MT6 in parallelo (due persone, due PR), entrambe review-abili indipendentemente perché toccano endpoint diversi (`POST /documents` vs `DELETE /graph`) pur condividendo l'import da `infra/bus.py`.

## Verifica finale end-to-end (dopo tutti i merge)

- `cd backend && pytest -q` (full, senza filtro) → zero falliti (o solo skip espliciti da MT2).
- `cd frontend && npm test && npm run lint && npm run build`.
- Scenario manuale 1: due tab, ingest quasi simultanee → 409 sulla seconda, un solo documento risulta ingerito.
- Scenario manuale 2: ingest lungo, "Svuota KB" a metà → grafo a 0 nodi anche dopo il tempo di completamento naturale del job.
- Ri-generare `backend/_mt10_measure.json` con lo stesso documento `sole-e-vento` (doc_id corretto da MT3) e confermare che tutti gli 8 criteri restano PASS.

## Domande aperte per il team/PO (bloccanti solo per MT2 e MT6, non per iniziare MT1/MT3/MT4)

1. MT2 — skip permanente o cancellazione dei test Metagraph?
2. MT6 — cancellare il job attivo e procedere con lo svuotamento, o bloccare lo svuotamento finché il job non termina?
3. MT5 — rifiuto esplicito (409) o accodamento della seconda richiesta d'ingest?

Le raccomandazioni di questo documento (skip, cancella-e-procedi, rifiuto esplicito) sono le opzioni a minor complessità implementativa e più coerenti con lo stato attuale del codice; in assenza di indicazioni contrarie, il team può procedere con queste.
