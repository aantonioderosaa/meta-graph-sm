# Milestone 1 — Motore del grafo dei fatti

> **Obiettivo unico di questo milestone:** costruire e interrogare il **grafo dei fatti** ispirato a Supermemory, in versione minima ma corretta. Niente overlay Metagraph, niente giudice, niente fusione delle due architetture, niente oblio. Un passo alla volta.
>
> **Cosa consegna:** ingestione → creazione del grafo dei fatti → dreaming molto semplice (senza oblio) → catene di fatti costruite con le relazioni note (`updates`/`extends`/`derives`) → `isLatest` corretto → query sul grafo.

---

## 0. Scope

**Dentro il milestone**
- Ingestione di documenti (chunking + estrazione fatti atomici + filtro rumore).
- Modello dati del solo grafo dei fatti.
- Dreaming semplice: raggruppamento di documenti correlati, consolidamento in fatti puliti, rilevazione delle relazioni fra fatti.
- Costruzione e manutenzione delle **catene di fatti** via `updates`/`extends`/`derives`.
- Corretto funzionamento di **`isLatest`** (invariante + mantenimento incrementale).
- Query sul grafo che rispetta `isLatest` (stato corrente vs storico).

**Fuori dal milestone (rimandato ai successivi)**
- Overlay Metagraph: entità tipizzate, relazioni tipizzate, domini, Strati 1/2.
- Giudice (anti-blur, riconciliazione, sincronizzazione).
- `forgetAfter` / oblio.
- Meccanismo di confidenza + soglia + promozione.
- `contradicts` e altre relazioni speciali (in questa fase un conflitto si tratta come `updates` per recency).
- Entity-linking massivo / chiusura degli attestati.

Stack di riferimento: Postgres + `pgvector`, un LLM per estrazione/consolidamento/classificazione relazioni, embedding locali (`bge-base-en-v1.5` o Ollama).

---

## 1. Modello dati (solo grafo dei fatti)

| Tabella | Campi | Note |
|---|---|---|
| `chunks` | `id, doc_id, text, embedding, created_at` | `doc_id` = customId della sorgente |
| `facts` | `id, text, type, is_latest, confidence, source_doc_id, embedding, created_at` | `type ∈ {fact, preference, episode}` |
| `fact_provenance` | `fact_id, chunk_id` | molti-a-molti: un fatto nasce da ≥1 chunk |
| `fact_edges` | `id, src_fact_id, tgt_fact_id, type, created_at` | `type ∈ {updates, extends, derives}` |

Note sui campi:
- `is_latest` (bool): il fatto è la versione corrente. È il campo che questo milestone deve tenere sempre coerente.
- `confidence` (float): lasciato come colonna con default `1.0`. **Nessuna logica di promozione in questo milestone** — è solo un valore memorizzato.
- `forget_after`: **non incluso**. L'oblio è fuori scope. (Se vuoi, tieni la colonna nullable riservata per il futuro, ma non implementarne il job.)

**Direzione degli archi (convenzione fissa):** `src` è sempre il fatto **nuovo/derivato**, `tgt` il **precedente/sorgente**.
- `updates(N → V)` = «N sostituisce V».
- `extends(N → V)` = «N arricchisce V».
- `derives(D → S)` = «D è astratto/inferito da S».

Indici utili: `facts(is_latest)`, indice vettoriale su `facts.embedding`, `fact_edges(tgt_fact_id, type)`.

---

## 2. Ingestione (write-path veloce)

**Cosa:** portare un documento a fatti atomici memorizzati, senza ancora ragionare su relazioni.

**2.1 Chunking.** Splitter ricorsivo ~256–512 token con overlap 10–15% (o per frase se i documenti sono corti/strutturati). Per ogni chunk: calcola l'embedding, scrivi in `chunks` con il `doc_id`. Nessun LLM qui.

**2.2 Estrazione fatti + filtro rumore.** Una chiamata LLM per chunk (o piccolo gruppo contiguo) con output JSON vincolato:

```json
{ "facts": [ { "text": "...", "type": "fact|preference|episode" } ] }
```

Prompt: estrai fatti atomici, ignora chiacchiere e conferme vuote, classifica ciascun fatto. Se `facts` è vuoto → il chunk era rumore, scartato. Ogni fatto va in `facts` con `is_latest = true` (provvisorio), `confidence = 1.0`, e viene collegato ai suoi chunk in `fact_provenance`.

> A fine ingestione i fatti esistono ma sono ancora "grezzi": nessuna relazione, `is_latest` non ancora riconciliato con la KB. Ci pensa il dreaming.

---

## 3. Dreaming semplice (senza oblio)

Gira **a batch** dopo l'ingestione (per un primo cut può girare anche sincrono subito dopo, se non vuoi uno scheduler). Tre passi, in ordine.

**3.1 Raggruppamento di fatti correlati.**
Raggruppa i fatti freschi prima per `doc_id`, poi per vicinato di embedding (kNN con soglia coseno ~0.80, oppure connected-components su un grafo di similarità). Ogni gruppo è un'unità coerente.

**3.2 Consolidamento (+ `derives`).**
Una chiamata LLM per gruppo che fonde frammenti duplicati/sparsi in fatti più puliti e di livello più alto.
- Se il consolidamento produce un'**astrazione** D da un pattern di fatti `S1..Sn`, scrivi D come nuovo fatto (`is_latest = true`) con `derives(D → Si)` per ogni sorgente, e `fact_provenance(D)` = unione dei chunk sorgente. I fatti sorgente **restano** (non vengono toccati nell'`is_latest`).
- Se il consolidamento produce solo una versione più pulita di un fatto esistente, trattala come un normale candidato per la rilevazione relazioni (§3.3).

**3.3 Rilevazione relazioni + costruzione catene.**
Per ogni fatto nuovo/consolidato **N**, cerca i candidati correlati **solo tra i fatti `is_latest = true`** (kNN top-k). Questo è il vincolo che tiene pulite le catene: non si confronta mai con record storici già superati. Per ogni candidato **V**, l'LLM classifica la relazione di N verso V:

| Esito | Azione | Effetto su `is_latest` |
|---|---|---|
| `replaces` | `fact_edges(updates, N→V)` | `V.is_latest = false`, `N.is_latest = true` |
| `extends` | `fact_edges(extends, N→V)` | entrambi restano `true` |
| `none` | nessun arco | invariato |

Dettaglio catene:
- **Catena di `updates`** (`A ← B ← C`): quando C sostituisce B (che già sostituiva A), solo C resta `is_latest`. La riconciliazione con i soli fatti correnti garantisce che N agganci sempre la **testa** della catena, mai un nodo storico.
- **Grappolo di `extends`**: più fatti complementari, tutti `is_latest = true`, collegati fra loro — insieme formano il quadro completo di un'entità/situazione.

> **Nessun oblio in questo milestone.** Non c'è scadenza né cancellazione: i fatti storici restano, marcati `is_latest = false`, e servono a ricostruire lo storico. La pulizia è solo logica (flag), non fisica.

---

## 4. Le relazioni e le catene di fatti (semantica precisa)

Le tre relazioni note, tutte **interne al grafo dei fatti** (nessuna richiede il metagraph):

- **`updates` (+ `supersedes` / `isLatest`).** Il fatto nuovo **contraddice/sostituisce** il vecchio. Genera la catena temporale. Il lato "semantico" (`supersedes`) e il flag (`isLatest`) sono due viste della stessa cosa: chi ha un `updates` che punta a sé **non** è più latest.
- **`extends`.** Il fatto nuovo **arricchisce senza sostituire**. Non cambia `isLatest`: entrambi restano correnti. Serve a comporre informazioni complementari senza perdere nulla.
- **`derives`.** Il fatto è **astratto/inferito da un pattern** di altri fatti (prodotto dal consolidamento, §3.2). Coesiste con le sue sorgenti; ne conserva la provenienza.

Regola d'oro delle catene: **la rilevazione relazioni confronta N solo con i fatti `is_latest = true`.** Da qui discende, gratis, che le catene di `updates` restano lineari e senza rami spuri.

---

## 5. `isLatest` — invariante e mantenimento

**Invariante (definizione dichiarativa).** Un fatto `X` è corrente se e solo se **nessun altro fatto lo aggiorna**:

```
X.is_latest  ⇔  NOT EXISTS ( fact_edges e : e.type = 'updates' AND e.tgt_fact_id = X.id )
```

`extends` e `derives` **non** influenzano `isLatest`. Solo `updates` lo abbassa.

**Mantenimento incrementale (on write).** Quando scrivi `updates(N → V)`:
1. `V.is_latest = false`;
2. `N.is_latest = true` (salvo che, nello stesso batch, qualcosa aggiorni N — in tal caso l'ordine di applicazione lo sistema, oppure lo fa la ricomputazione sotto).

**Ricomputazione (verifica/riparazione).** Query di verifica che deve sempre coincidere con i flag incrementali — utile come test e come riparazione batch:

```sql
UPDATE facts f
SET is_latest = NOT EXISTS (
  SELECT 1 FROM fact_edges e
  WHERE e.type = 'updates' AND e.tgt_fact_id = f.id
);
```

Se questa `UPDATE` cambia qualche riga, i flag incrementali erano andati fuori sync: è il tuo canarino di correttezza.

**Caso limite da testare:** N corrisponde semanticamente a un fatto **già superato** (storico). Poiché §3.3 confronta N solo con i correnti, N aggancia la testa della catena, non il nodo storico → `isLatest` resta corretto. È il test più importante del milestone.

---

## 6. Query sul grafo

**Default (stato corrente):** rispondi solo con i fatti correnti.
1. Embedda la query.
2. Ricerca vettoriale su `facts` **filtrando `is_latest = true`** (più eventuale filtro su `type`).
3. Espandi il risultato per completezza: dai fatti trovati, tira i vicini via `extends` (correnti) per ricomporre il quadro completo, e includi l'eventuale astrazione `derives` collegata.
4. Passa l'insieme all'LLM per formulare la risposta, portando la provenienza (`fact_provenance` → `chunks`, `source_doc_id`).

**Query storica (opzionale ma già supportata dai dati):** per "com'era prima / come è cambiato", parti dal fatto corrente e **risali la catena `updates`** (`src → tgt`) fino all'origine. I flag e gli archi ci sono già; è solo un traversal diverso.

> Il grafo dei fatti è **temporale**: lo stato corrente è la vista `is_latest = true`, lo storico è la catena `updates`. Questo milestone deve rispondere correttamente a entrambe.

---

## 7. Ordine di implementazione

1. Modello dati (§1) — tabelle + indici.
2. Ingest + chunking (§2.1).
3. Estrazione fatti + filtro rumore (§2.2).
4. Dreaming: raggruppamento (§3.1) → consolidamento/`derives` (§3.2) → relazioni + `isLatest` (§3.3, §5).
5. Query corrente + storica (§6).
6. Test di accettazione (§8).

---

## 8. Criteri di accettazione (il milestone è "fatto" quando)

- [ ] Ingerire un documento crea `chunks` + `facts` con `fact_provenance` corretta; il rumore viene scartato.
- [ ] Due fatti in cui uno **sostituisce** l'altro producono `updates`, il vecchio va a `is_latest=false`, il nuovo resta `true`; la query corrente restituisce **solo** il nuovo.
- [ ] Una catena di 3 sostituzioni (`A ← B ← C`) lascia `is_latest=true` **solo** su C; lo storico è percorribile risalendo `updates`.
- [ ] Due fatti complementari producono `extends`, restano **entrambi** correnti, e la query corrente li restituisce insieme.
- [ ] Il consolidamento produce un'astrazione con archi `derives` verso le sorgenti, che **restano** presenti.
- [ ] Un aggiornamento che riguarda un fatto **già superato** aggancia la **testa** della catena, non il nodo storico (`isLatest` resta corretto).
- [ ] La ricomputazione `isLatest` (§5) **non** cambia alcuna riga dopo un ciclo di dreaming: flag incrementali e invariante coincidono.
- [ ] La query storica ricostruisce l'evoluzione di un fatto risalendo la catena `updates`.

---

## 9. Note per i milestone successivi (non implementare ora)

- `contradicts` (conflitto genuino da **preservare** invece di risolvere per recency) — raffinamento della §3.3.
- `forgetAfter` / oblio — job schedulato separato.
- Overlay Metagraph (entità/relazioni tipizzate, domini, Strati 1/2) — si costruisce **sopra** questo grafo, dopo.
- Giudice, confidenza/promozione, entity-linking massivo — fasi dedicate.
