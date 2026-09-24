# Piano di fix — regressioni introdotte nell'implementazione di PIANO-FIX-INGESTIONE-QA.md

Questo piano nasce da una verifica puntuale dell'implementazione del piano precedente (`PIANO-FIX-INGESTIONE-QA.md`). Il problema segnalato ("`API DELETE /event-graph/graph failed with 503`") è solo il sintomo più visibile: la verifica ha trovato un bug più grave nello stesso punto (l'ingestione è **completamente rotta**, non solo lo svuotamento), una regressione nuova nel fix della fattualità, un'implementazione incompleta del guard lato frontend, file di debug dimenticati nel repo, e un bug di sintassi pre-esistente (non causato da questo lavoro) che impedisce di eseguire l'intera suite in un colpo solo.

Ogni macrotask sotto riporta: il codice **esatto** oggi presente nel file (verificato leggendo il file, non a memoria), il problema preciso, e il codice **esatto** con cui sostituirlo. Non è richiesto reinterpretare nulla: applicare le sostituzioni come scritte, poi eseguire i comandi di verifica indicati.

## Mappa delle dipendenze

```
MT1  Fix import mancante (rompe ingest E wipe)        ─┐
MT2  Fix cancel_job() che non aspetta davvero          ─┤  stesso file/area, MT2 richiede MT1 per essere testato end-to-end
MT3  Fix regressione fattualità (factuality.py)        ── indipendente
MT4  Frontend: guard UI ingestione + messaggio 409     ── indipendente
MT5  Pulizia file di debug dimenticati nel repo        ── indipendente, banale
MT6  Fix sintassi test_event_graph_ancore_smistamento  ── indipendente, pre-esistente, ma sblocca la verifica di tutto il resto
MT7  (fuori scope) m10/m14 — da assegnare separatamente
```

MT1 e MT2 toccano gli stessi due file (`infra/bus.py`, `api/event_graph.py`) — conviene farli nella stessa PR, nell'ordine scritto qui. Tutti gli altri sono indipendenti e assegnabili in parallelo. **Fai MT6 per primo o in parallelo fin da subito**: senza quel fix, `pytest` (senza filtrare i singoli file) fallisce alla raccolta e nessuno degli altri macrotask può essere verificato con la suite completa.

---

## MT1 — Fix import mancante: rotto sia `POST /documents` (500) sia `DELETE /graph` (503)

**File:** `backend/app/api/event_graph.py`

**Causa.** Il file usa `has_running_job()`, `cancel_job()`, `register_running_task()` (righe ~95, ~103, ~135-137) ma l'import da `infra.bus` in cima al file non è stato aggiornato quando queste funzioni sono state aggiunte.

**Verificato:** `POST /event-graph/documents` fallisce con `500 Internal Server Error` (`NameError: name 'has_running_job' is not defined`, non catturato da nessun try/except in quella funzione) — quindi **in questo momento nessuna ingestione può partire**, non solo lo svuotamento. Confermato anche da due test già esistenti nel repo che ora falliscono: `tests/test_event_graph_jobs.py::test_post_documents_registers_job` e `tests/test_event_graph_m12.py::test_post_documents_returns_job_id_without_legacy_bus`.

**Codice attuale (righe 27-35):**
```python
from app.pipeline.event_graph.infra.bus import (
    elenca_job,
    merge_job_lists,
    register_job,
    reset_event_bus,
    run_tracked_job,
    subscribe,
    unsubscribe,
)
```

**Sostituire con:**
```python
from app.pipeline.event_graph.infra.bus import (
    cancel_job,
    elenca_job,
    has_running_job,
    merge_job_lists,
    register_job,
    register_running_task,
    reset_event_bus,
    run_tracked_job,
    subscribe,
    unsubscribe,
)
```

**Verifica.**
```bash
cd backend
pytest -q tests/test_event_graph_jobs.py tests/test_event_graph_m12.py tests/test_event_graph_documents_wipe.py
```
Tutti e tre i file devono risultare verdi (oggi `test_event_graph_documents_wipe.py::test_get_documents_and_delete_graph` fallisce con `503`, gli altri due con `NameError`).

Manuale: avviare backend + frontend, cliccare "Ingerisci" con un testo qualsiasi → deve tornare un `job_id` (non un errore).

---

## MT2 — `cancel_job()` non aspetta davvero la cancellazione del task

**File:** `backend/app/pipeline/event_graph/infra/bus.py`

**Causa.** `cancel_job` è una funzione **sincrona** (`def`, non `async def`) che al suo interno chiama `asyncio.wait_for(task, timeout=1.0)` **senza `await`**. Senza `await`, quella riga crea una coroutine che non viene mai eseguita — non aspetta nulla, e (in produzione, fuori da pytest) genera anche un `RuntimeWarning: coroutine 'wait_for' was never awaited`. L'unica cosa che oggi dà un minimo di margine alla cancellazione è il `await asyncio.sleep(0.1)` fisso nel chiamante (`wipe_event_graph`), indipendente da quanto impiega il task a fermarsi davvero.

**Codice attuale (`infra/bus.py`):**
```python
def cancel_job(job_id: str) -> None:
    """Cancel a running job by its ID.
    
    If the task exists and is not already completed/failed, it will be cancelled.
    No-op if no such task exists or if it's already finished.
    """
    task = _active_tasks.get(job_id)
    if task and not task.done():
        # Cancel the task properly
        task.cancel()
        try:
            # Wait for the cancellation to complete with a timeout
            asyncio.wait_for(task, timeout=1.0)  # 1 second timeout
        except asyncio.TimeoutError:
            pass  # Task didn't finish in time but was cancelled
        except asyncio.CancelledError:
            pass  # Expected when task is cancelled
```

**Sostituire con:**
```python
async def cancel_job(job_id: str) -> None:
    """Cancel a running job by its ID and wait (briefly) for it to unwind.

    If the task exists and is not already completed/failed, it is cancelled
    and awaited with a timeout. No-op if no such task exists or it's already
    finished.
    """
    task = _active_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            pass
```

(unica differenza: `def` → `async def`, e `await` aggiunto davanti a `asyncio.wait_for(...)`.)

**File:** `backend/app/api/event_graph.py` — il chiamante deve ora fare `await`:

**Codice attuale (dentro `wipe_event_graph`):**
```python
            running_job_id = has_running_job()
            if running_job_id is not None:
                cancel_job(running_job_id)
                # Wait a brief moment to allow cancellation (this is a best-effort approach)
                await asyncio.sleep(0.1)
            
            await wipe_grafo(session)
```

**Sostituire con:**
```python
            running_job_id = has_running_job()
            if running_job_id is not None:
                await cancel_job(running_job_id)

            await wipe_grafo(session)
```

(il `cancel_job` ora aspetta davvero fino a 1s tramite `wait_for` al suo interno, quindi il flat `sleep(0.1)` esterno non serve più — rimuoverlo evita di dare un falso senso di sicurezza su un timeout scollegato dal reale stato del task.)

**Nota opzionale, non bloccante.** `_active_tasks` non viene mai ripulito per i job che terminano normalmente (solo `reset_event_bus()` lo svuota). Non è un bug (i controlli su `task.done()` lo gestiscono correttamente), ma è una crescita di memoria non necessaria in un processo di lunga durata; se il team vuole, si può rimuovere l'entry da `_active_tasks` alla fine di `run_tracked_job` in un `finally`. Facoltativo, non richiesto per chiudere questo macrotask.

**Verifica.** Scrivere (o adattare da `test_event_graph_documents_wipe.py`) un test che: registra un job con un task reale e lento (es. `asyncio.sleep(5)` avvolto in `run_tracked_job`), chiama `DELETE /event-graph/graph` mentre il task è ancora attivo, e verifica che la chiamata torni entro ~1.1s (non 5s) e che `elenca_job()` non mostri il job ricomparire dopo.

---

## MT3 — Regressione nella fattualità (`factuality.py`): non risolve il bug originale e ne introduce uno nuovo

**File:** `backend/app/pipeline/event_graph/factuality.py`

**Stato verificato:** rieseguendo `pytest tests/test_event_graph_m4.py` risultano **3 falliti** (2 dei 5 originali sono stati sistemati), più **1 test che prima passava e ora fallisce** (`test_ruolo_se_is_ipotetico_and_wins_over_non_fattuale`), più **danno collaterale** su `tests/test_event_graph_m_micro1.py::test_modalita_feeds_factuality_table`.

**Causa esatta.** Il fix ha riordinato troppo aggressivamente `_local_fattualita`, spostando `modalizzato` e `polarita_negata` **sopra** i controlli `ruolo_se`/`modalita ipotetico` (che devono restare per primi — è il senso esplicito del test che ora fallisce: "ruolo_se ... wins over non_fattuale"). Inoltre ha aggiunto un controllo che legge `parent.classe_verbo_reggente` (il campo del **padre**), ma quel campo descrive il verbo reggente **del figlio**, non del padre: non scatta mai, quindi il caso di un figlio incorporato (`completiva_di` che risolve a un padre reale) sotto un padre `NON_FATTUALE`/`IPOTETICO` non eredita mai la fattualità del padre.

**Codice attuale — sostituire l'intero blocco da `_is_realized_assertion` fino alla fine di `_local_fattualita` (righe 34-103) con quanto segue.**

Codice attuale (da rimuovere):
```python
def _is_realized_assertion(event: EventoRisolto) -> bool:
    """Main/coord finite past-present + modalita fattuale is a realized event.

    LLM leftover ``finale`` / ``completiva_di`` on "si tolse" must not demote
    the climax to NON_FATTUALE.
    
    But if an event has finale=True, it should never be considered realized in this sense.
    """
    # If this is a final event, don't consider it as realized assertion regardless of other factors
    if getattr(event, "finale", False):
        return False
        
    seg = getattr(event, "segmentazione", None)
    return (
        _modalita(event) == "fattuale"
        and not event.polarita_negata
        and event.ruolo_se == "nessuno"
        and event.tempo in _REALIZED_TEMPI
        and (seg is None or seg in _MAIN_FINITE_SEGS)
    )


def _local_fattualita(event: EventoRisolto, parent_of: dict[object, EventoRisolto] | None = None) -> Fattualita:
    # Handle modalizzato as a condition independent of modalita
    if event.modalizzato:
        return "NON_FATTUALE"
    if event.polarita_negata:
        return "NON_FATTUALE"
    if event.frase_tipo in _NON_FATTUALE_FRASI:
        return "NON_FATTUALE"
    if event.tempo == "futuro":
        return "NON_FATTUALE"
    
    # Check for IPOTETICO first
    if event.ruolo_se != "nessuno":
        return "IPOTETICO" 
    if _modalita(event) == "ipotetico":
        return "IPOTETICO"
        
    # Handle the special case for non_fattivo verbs with completiva_di  
    # If an event has a real parent and it's syntactically embedded, check conditions
    if (
        event.completiva_di is not None
        and event.classe_verbo_reggente == "non_fattivo"
    ):
        return "NON_FATTUALE"
    
    # Handle the case for fattivo verbs with completiva_di 
    # If an embedded event has classe_verbo_reggente="fattivo", it should be FATTUALE
    if (
        event.completiva_di is not None
        and event.classe_verbo_reggente == "fattivo"
    ):
        return "FATTUALE"
    
    # Now check realized assertions  
    if _is_realized_assertion(event):
        # If we have parent information, this might be syntactically embedded
        if parent_of is not None:
            key = _key(event)
            parent = parent_of.get(key)
            if parent is not None and getattr(parent, 'classe_verbo_reggente', None) == "non_fattivo":
                # If parent has classe_verbo_reggente="non_fattivo", embedded events should be NON_FATTUALE
                return "NON_FATTUALE"
        return "FATTUALE"
    
    if event.finale:
        return "NON_FATTUALE"
        
    return "FATTUALE"
```

**Sostituire con:**
```python
def _is_realized_assertion(event: EventoRisolto, has_real_parent: bool = False) -> bool:
    """Main/coord finite past-present + modalita fattuale is a realized event.

    LLM leftover ``finale`` / ``completiva_di`` on "si tolse" must not demote
    the climax to NON_FATTUALE — ma questa immunità vale SOLO per un evento
    che non è sintatticamente imbarcato sotto un padre reale. Un evento con
    ``completiva_di`` che risolve a un padre effettivo (``has_real_parent``)
    non è mai considerato "realizzato" in questo senso: la sua fattualità
    dipende dal padre (vedi ``_inherit_down``), non dalla propria forma
    verbale superficiale.
    """
    if has_real_parent:
        return False
    if getattr(event, "finale", False):
        return False
    if event.modalizzato:
        return False
    if event.frase_tipo in _NON_FATTUALE_FRASI:
        return False
    seg = getattr(event, "segmentazione", None)
    return (
        _modalita(event) == "fattuale"
        and not event.polarita_negata
        and event.ruolo_se == "nessuno"
        and event.tempo in _REALIZED_TEMPI
        and (seg is None or seg in _MAIN_FINITE_SEGS)
    )


def _local_fattualita(
    event: EventoRisolto,
    parent_of: dict[object, EventoRisolto] | None = None,
) -> Fattualita:
    if event.ruolo_se != "nessuno":
        return "IPOTETICO"
    if _modalita(event) == "ipotetico":
        return "IPOTETICO"
    if event.polarita_negata:
        return "NON_FATTUALE"
    if _modalita(event) in {"volitivo", "deontico"}:
        return "NON_FATTUALE"
    if event.modalizzato and _modalita(event) != "fattuale":
        return "NON_FATTUALE"
    has_real_parent = parent_of is not None and _key(event) in parent_of
    if _is_realized_assertion(event, has_real_parent):
        return "FATTUALE"
    if event.modalizzato:
        return "NON_FATTUALE"
    if (
        event.completiva_di is not None
        and event.classe_verbo_reggente == "non_fattivo"
    ):
        return "NON_FATTUALE"
    if event.finale:
        return "NON_FATTUALE"
    if event.frase_tipo in _NON_FATTUALE_FRASI:
        return "NON_FATTUALE"
    if event.tempo == "futuro":
        return "NON_FATTUALE"
    return "FATTUALE"
```

Nota: questa è, a parte l'aggiunta del parametro `has_real_parent`, **la stessa identica funzione `_local_fattualita` della versione originale del file** (prima di qualunque fix) — l'unico cambiamento reale è che `_is_realized_assertion` ora sa se l'evento è imbarcato sotto un padre reale, e in tal caso non concede mai l'immunità. Non e' stato necessario un riordino diverso da quello originale: i tre casi che il fix precedente cercava di coprire spostando i controlli (`modalizzato` generico, `frase_tipo`, `finale`) sono ora gestiti **dentro** `_is_realized_assertion` stessa, con lo stesso pattern già usato lì per `finale`.

**Secondo punto — `_inherit_down`, un solo cambio nella riga della guardia:**

**Codice attuale (dentro `_inherit_down`, circa riga 161-166):**
```python
        if (
            parent is not None
            and parent.fattualita in _INHERITED
            and not _is_realized_assertion(current)
        ):
            current.fattualita = parent.fattualita
```

**Sostituire con:**
```python
        if (
            parent is not None
            and parent.fattualita in _INHERITED
            and not _is_realized_assertion(current, has_real_parent=True)
        ):
            current.fattualita = parent.fattualita
```

(`has_real_parent=True` è corretto qui perché questo ramo esegue solo quando `parent is not None`, cioè quando `current` ha già un padre risolto — è esattamente la stessa condizione.)

**Verifica.**
```bash
cd backend
pytest -q tests/test_event_graph_m4.py tests/test_event_graph_m_micro1.py
```
Tutti i test di `test_event_graph_m4.py` (18 in totale) devono passare, incluso `test_ruolo_se_is_ipotetico_and_wins_over_non_fattuale` (che con il fix precedente era regredito) e i 3 ancora falliti oggi (`test_inheritance_descends_and_never_climbs`, `test_factsheet_span_fallback_without_indice_grezzo`, e verificare anche `test_span_reorder_resolves_completiva_via_indice_grezzo` e `test_completiva_non_fattivo_vs_fattivo_exception` che non regrediscano). Verificare esplicitamente anche `test_modalita_feeds_factuality_table` in `test_event_graph_m_micro1.py` — non è stato tracciato passo-passo in questa analisi, va confermato a parte.

Poi, suite estesa per escludere effetti collaterali:
```bash
pytest -q tests/test_event_graph_*.py tests/test_acceptance_event_graph_e2e.py --ignore=tests/test_event_graph_integration.py --ignore=tests/test_event_graph_ancore_smistamento.py
```
(l'ultimo `--ignore` è necessario solo finché MT6 non è applicato — vedi sotto. Da questo comando, tramite `ls`/glob, escludere a mano il file se la shell espande il glob prima di pytest, es. `files=$(ls tests/test_event_graph_*.py | grep -v ancore_smistamento); pytest -q $files ...`.)

---

## MT4 — Frontend: il bottone "Ingerisci" non si disabilita durante un'ingestione, e il messaggio di errore 409 mostra "undefined"

### 4a. Bottone non disabilitato durante la pipeline

**Causa.** `EventIngestPanel.tsx` disabilita il bottone "Ingerisci" solo con `busy` (vero solo durante la chiamata HTTP di submit, che dura una frazione di secondo) — non per tutta la durata della pipeline. `EventGraphShell.tsx` però **ha già** esattamente il segnale giusto: lo stato `graphLive` (righe 82, 132, passato oggi solo a `EventIngestPanel`... in realtà no, verificato: oggi va solo a un componente della UI del grafo, non a `EventIngestPanel`). `graphLive` diventa `true` quando c'è un job con stato diverso da `done`/`failed` (vedi `frontend/lib/event-graph/live-graph.ts:58-61`, funzione `jobIsLive`) e torna `false` sia su completamento sia su fallimento. È il segnale corretto, va solo propagato a `EventIngestPanel`.

**File:** `frontend/components/event-graph/EventGraphShell.tsx`

**Codice attuale (circa righe 353-356):**
```tsx
          <EventIngestPanel
            onJobStarted={setJobId}
            onWiped={handleWiped}
          />
```

**Sostituire con:**
```tsx
          <EventIngestPanel
            onJobStarted={setJobId}
            onWiped={handleWiped}
            ingestionInCorso={graphLive}
          />
```

**File:** `frontend/components/event-graph/EventIngestPanel.tsx`

**Codice attuale (righe 28-31):**
```tsx
type EventIngestPanelProps = {
  onJobStarted?: (jobId: string) => void;
  onWiped?: () => void;
};
```

**Sostituire con:**
```tsx
type EventIngestPanelProps = {
  onJobStarted?: (jobId: string) => void;
  onWiped?: () => void;
  ingestionInCorso?: boolean;
};
```

**Codice attuale (riga 33-36):**
```tsx
export function EventIngestPanel({
  onJobStarted,
  onWiped,
}: EventIngestPanelProps) {
```

**Sostituire con:**
```tsx
export function EventIngestPanel({
  onJobStarted,
  onWiped,
  ingestionInCorso = false,
}: EventIngestPanelProps) {
```

**Codice attuale (righe 136-142, bottone "Ingerisci"):**
```tsx
            <Button
              type="submit"
              size="sm"
              disabled={busy || wiping || !text.trim() || !docId.trim()}
            >
              {busy ? "Invio…" : "Ingerisci"}
            </Button>
```

**Sostituire con:**
```tsx
            <Button
              type="submit"
              size="sm"
              disabled={
                busy || wiping || ingestionInCorso || !text.trim() || !docId.trim()
              }
            >
              {busy ? "Invio…" : ingestionInCorso ? "Ingestione in corso…" : "Ingerisci"}
            </Button>
```

**Nota su un residuo non bloccante.** C'è una finestra di un singolo render React tra "la POST è tornata e `busy` torna `false`" e "`graphLive` diventa `true`" (quest'ultimo dipende dagli effect di `EventPipelineMonitor` che girano in un render successivo). In pratica è una finestra di pochi millisecondi, non cliccabile a mano; il guard server-side (MT1, il 409) resta comunque l'unica fonte di verità — questo è solo un miglioramento di UX, non la difesa reale.

### 4b. Il messaggio di errore 409 mostra "undefined" al posto del job_id

**Causa.** FastAPI incapsula `HTTPException(status_code=409, detail={"detail": "...", "job_id": "..."})` in un ulteriore livello: il body JSON reale è `{"detail": {"detail": "...", "job_id": "..."}}` (verificato con una chiamata diretta). Il frontend legge `err.body?.job_id`, che non esiste a quel livello — il vero percorso è `err.body.detail.job_id`.

**File:** `backend/app/api/event_graph.py` — rinominare la chiave interna da `detail` a `message` per evitare la doppia dicitura "detail.detail" (più leggibile per chi legge i log):

**Codice attuale (dentro `ingest_event_graph_document`):**
```python
    existing_job = has_running_job()
    if existing_job is not None:
        raise HTTPException(
            status_code=409,
            detail={"detail": "Ingestione già in corso", "job_id": existing_job}
        )
```

**Sostituire con:**
```python
    existing_job = has_running_job()
    if existing_job is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "Ingestione già in corso", "job_id": existing_job},
        )
```

**File:** `frontend/components/event-graph/EventIngestPanel.tsx`

**Codice attuale (righe 61-69, dentro `onSubmit`):**
```tsx
    } catch (err) {
      // Handle 409 Conflict error
      if (err && typeof err === 'object' && 'status' in err && err.status === 409) {
        const detail = (err as any).body?.detail;
        const activeJobId = (err as any).body?.job_id;
        setError(`Ingestione già in corso per il job ${activeJobId}.`);
      } else {
        setError(err instanceof Error ? err.message : "Ingestione fallita");
      }
    } finally {
```

**Sostituire con:**
```tsx
    } catch (err) {
      if (err instanceof EventGraphApiError && err.status === 409) {
        const body = err.body as { detail?: { message?: string; job_id?: string } } | null;
        const activeJobId = body?.detail?.job_id;
        setError(
          activeJobId
            ? `Ingestione già in corso per il job ${activeJobId}.`
            : "Ingestione già in corso.",
        );
      } else {
        setError(err instanceof Error ? err.message : "Ingestione fallita");
      }
    } finally {
```

E aggiungere `EventGraphApiError` all'import già presente in cima al file:

**Codice attuale (righe 20-24):**
```tsx
import {
  fetchDocuments,
  ingestDocument,
  wipeKnowledgeBase,
} from "@/lib/event-graph/api";
```

**Sostituire con:**
```tsx
import {
  EventGraphApiError,
  fetchDocuments,
  ingestDocument,
  wipeKnowledgeBase,
} from "@/lib/event-graph/api";
```

**Verifica.** Manuale: avviare un'ingestione, mentre è in corso tentare di avviarne una seconda (secondo tab o click rapido dopo aver riabilitato temporaneamente il bottone da devtools) → il messaggio deve mostrare l'id del job reale, non "undefined", e il bottone deve restare disabilitato con etichetta "Ingestione in corso…" per tutta la durata della prima pipeline. Aggiungere/aggiornare un test in `frontend/lib/event-graph/api.test.ts` o in un test del componente che copra la lettura di `err.body.detail.job_id`.

---

## MT5 — Rimuovere i file di debug dimenticati nel repo

Non tracciati da git, presenti nella working copy, non fanno parte della suite né del prodotto:

```
debug_completiva.py
debug_detailed.py
debug_finale.py
debug_is_realized.py
debug_test.py
final_debug.py
test_concurrency.py
verify_implementation.py
backend/tests/test_event_graph_tasks.py
```

`backend/tests/test_event_graph_tasks.py` in particolare **non va solo rimosso per pulizia**: è una prima versione, rotta, della stessa suite di test poi riscritta correttamente in `backend/tests/test_event_graph_tasks_simple.py` (quest'ultimo: 5/5 verdi, da tenere). La versione rotta fallisce con 5 errori distinti (await mancanti su chiamate async, `asyncio.create_task` invocato fuori da un test asincrono) e, se lasciata, sporca ogni run completo della suite.

**Comando:**
```bash
rm debug_completiva.py debug_detailed.py debug_finale.py debug_is_realized.py debug_test.py final_debug.py test_concurrency.py verify_implementation.py backend/tests/test_event_graph_tasks.py
```

**Verifica.** `git status` non deve più mostrare questi file; `pytest -q backend/tests/` non deve più raccogliere `test_event_graph_tasks.py`.

---

## MT6 — Bug di sintassi pre-esistente in `test_event_graph_ancore_smistamento.py` (non causato da questo lavoro, ma blocca la verifica di tutti gli altri macrotask)

**Causa.** Il file ha, dal commit `0ee89e0` (l'ultimo commit prima di qualunque modifica di questo piano — quindi non è colpa di questo lavoro), un blocco `try:` senza `except`/`finally` nella funzione `test_eccezione_llm_lascia_evento_senza_appartenenza_e_pubblica`. Questo impedisce a `pytest` di **raccogliere l'intero file**, e quindi blocca qualunque comando che lo includa (`pytest -q` senza filtri, o il comando documentato nel README con il glob `tests/test_event_graph_*.py`) con un `SyntaxError` invece di eseguire i test.

Il corpo mancante esisteva nel commit precedente (`71866a1`) ed è stato recuperato da lì — non è stato reinventato.

**Codice attuale (righe 356-378):**
```python
@pytest.mark.asyncio
async def test_eccezione_llm_lascia_evento_senza_appartenenza_e_pubblica():
    linea = _linea_carol()
    evento = _evento("e-fail", lemma="dire", span="disse", posizione_doc=12)
    job_id = "job-smista-fail"
    queue = await event_graph_bus.subscribe(job_id)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("llm down")

    try:
        esito = await smista_eventi(
            linea, [evento], job_id=job_id, call_structured=boom
        )
        assert esito is not None
        assert esito.appartenenze == []
        assert "e-fail" not in _membership(esito)
        assert esito.n_llm_fail >= 1
        assert esito.n_non_collocati == 1
        assert all(a.etichetta not in _ETICHETTE_VIETATE for a in esito.linea.ancore)


@pytest.mark.asyncio
async def test_tempo_mention_colloca_senza_llm():
```

**Sostituire con (recuperato da `git show 71866a1:backend/tests/test_event_graph_ancore_smistamento.py`):**
```python
@pytest.mark.asyncio
async def test_eccezione_llm_lascia_evento_senza_appartenenza_e_pubblica():
    linea = _linea_carol()
    evento = _evento("e-fail", lemma="dire", span="disse", posizione_doc=12)
    job_id = "job-smista-fail"
    queue = await event_graph_bus.subscribe(job_id)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("llm down")

    try:
        esito = await smista_eventi(
            linea, [evento], job_id=job_id, call_structured=boom
        )
        assert esito is not None
        assert esito.appartenenze == []
        assert "e-fail" not in _membership(esito)
        assert esito.n_llm_fail >= 1
        assert esito.n_non_collocati == 1
        assert all(a.etichetta not in _ETICHETTE_VIETATE for a in esito.linea.ancore)
        eventi = []
        while True:
            try:
                eventi.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        riepiloghi = [item for item in eventi if item["event"] == EVENTO]
        assert riepiloghi
        payload = riepiloghi[0]["payload"]
        assert riepiloghi[0]["stage"] == STAGE
        assert payload["n_eventi"] == 1
        assert payload["n_datati"] == 0
        assert payload["n_llm"] == 1
        assert payload["n_llm_fail"] >= 1
        assert payload["n_non_collocati"] == 1
    finally:
        await event_graph_bus.unsubscribe(job_id, queue)
        event_graph_bus.reset_event_bus()


@pytest.mark.asyncio
async def test_tempo_mention_colloca_senza_llm():
```

`asyncio`, `EVENTO` e `STAGE` sono già importati in cima al file corrente (`import asyncio` riga 6; `EVENTO, STAGE` importati da `app.pipeline.event_graph.ancore_smistamento` righe 22-23) — non serve aggiungere altri import.

**Attenzione — non dare per scontato che le asserzioni recuperate siano ancora valide.** Il codice di `ancore_smistamento.py` può essere cambiato dal commit `71866a1` ad oggi. Dopo aver applicato la sostituzione, eseguire:
```bash
cd backend
pytest -q tests/test_event_graph_ancore_smistamento.py -v
```
Se `test_eccezione_llm_lascia_evento_senza_appartenenza_e_pubblica` fallisce (es. sul contenuto di `payload`, o su `riepiloghi[0]["stage"]`), il payload pubblicato da `smista_eventi` su fallimento LLM è cambiato nel frattempo: in tal caso il test va aggiornato ai campi/valori attuali, non forzato a passare cambiando l'implementazione.

**Verifica finale di questo macrotask.**
```bash
cd backend
pytest -q --collect-only
```
Non deve più comparire nessun `SyntaxError` in fase di raccolta.

---

## MT7 — Fuori scope: 5 test falliti apparentemente legati al lavoro parallelo di rinomina Evento→Fatto

Durante la verifica, con tutti gli altri file esclusi via `--ignore`, sono emersi anche questi falliti, in file non toccati da nessuno dei macrotask sopra:

- `tests/test_event_graph_m10.py::test_determined_pair_writes_precede_earlier_to_later`
- `tests/test_event_graph_m10.py::test_refinement_superato_da_and_conflitto`
- `tests/test_event_graph_m10.py::test_fakesession_emits_merge_never_delete`
- `tests/test_event_graph_m10.py::test_esegui_session_none_in_memory`
- `tests/test_event_graph_m14.py::test_temporal_refinement_in_memory_placeholder_then_precede`

Toccano `query_structured.py` e `temporal_placement.py`, entrambi modificati nel lavoro parallelo di rinomina `:Evento`→`:Fatto` / kernel category (vedi `PIANO-FATTI-KERNEL.md`), non nel piano di fix qui trattato. **Non sono stati diagnosticati** in questa verifica — non attribuire questo piano come causa né come soluzione. Vanno assegnati a chi sta seguendo quel lavoro, con lo stesso livello di dettaglio (causa esatta + codice attuale + codice corretto) prima di essere chiusi.

---

## Sequenza consigliata

1. **Subito, in parallelo:** MT5 (pulizia, 2 minuti), MT6 (sblocca la suite completa per tutti).
2. **Subito dopo:** MT1 + MT2 insieme (stessa area di codice, stessa PR), MT3, MT4 — tre PR indipendenti in parallelo.
3. **A parte, da un altro team/persona:** MT7, dopo diagnosi separata.

## Verifica end-to-end finale (dopo tutti i merge di MT1-MT6)

```bash
cd backend
files=$(ls tests/test_event_graph_*.py)   # ora include anche ancore_smistamento, se MT6 è stato applicato
pytest -q $files tests/test_acceptance_event_graph_e2e.py --ignore=tests/test_event_graph_integration.py
```
Atteso: nessun fallito dovuto a MT1-MT6 (eventuali fallimenti residui di MT7 restano, tracciati a parte).

```bash
cd frontend
npm test && npm run lint && npm run build
```

Manuale:
- Avviare un'ingestione, cliccare "Svuota KB" mentre è ancora in corso → nessun `503`, il grafo resta vuoto anche dopo il tempo in cui l'ingestione sarebbe normalmente terminata.
- Avviare un'ingestione → il bottone "Ingerisci" resta disabilitato con etichetta "Ingestione in corso…" per tutta la pipeline, poi torna cliccabile a `done`/`failed`.
- Tentare una seconda ingestione forzando la richiesta (es. da un secondo tab) mentre la prima è in corso → risposta 409, messaggio con il vero `job_id` (non "undefined").
