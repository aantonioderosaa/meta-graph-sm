# Piano — "Cos'è un evento": gate delle asserzioni + identità stabile del soggetto

## Context

Ultima ingestione reale (`sole-e-vento`, favola "Il Sole e il Vento", ~340 parole
IT, FLASH off, 5 zone). Stato a grafo vs atteso:

| | valore | atteso |
|---|---|---|
| `:Evento` | **56** | ≈ 34 (una per verbo finito) |
| `:Menzione` | **54** | ≈ 25 (una per forma distinta) |
| `:Quarantena` | 4 (`rispose`, `Poi`, `imparò`, `è`) | eventi reali persi |

### Difetti dimostrati dal dump

1. **Eventi che non sono asserzioni.** Nodi `:Evento` per avverbi (`lentamente`,
   `facilmente`), per predicati non finiti (`brillare`, `scaldare`, `soffiare`,
   `proteggere`, `sapere`, `agire`, `avere`, `vedere` — `tempo=non_finito`), per
   copule/nomi nudi (`è`; `arrivare` con SOGG "il turno del Sole"), con soggetti
   non referenziali (`cercare` SOGG "il rispetto"). → nessuna mappa reale di
   soggetti/oggetti.
2. **Collasso aspettuale rotto.** `_prune_spurious_events`
   ([extraction.py:351-417](backend/app/pipeline/event_graph/extraction.py))
   droppa il verbo aspettuale ma lascia il figlio con `tempo=non_finito`: lemma
   sbagliato (`tolgere`), e `si tolse il mantello` (climax) marcato
   `NON_FATTUALE`.
3. **Identità del soggetto instabile.** `mention_coref._fuse_proper_names`
   ([mention_coref.py:199-222](backend/app/pipeline/event_graph/mention_coref.py))
   fonde solo `tipo_superficiale == "nome_proprio"`. "il Sole"/"il Vento" spesso
   taggati `sn_comune` (nomi comuni personificati) → mai fusi.
   `ids.menzione_id` ([ids.py:30-42](backend/app/pipeline/event_graph/ids.py))
   conia per i non-propri un id **per-occorrenza** → ogni ripetizione è un nodo
   nuovo per costruzione. Stesso soggetto nominato N volte → conteggio N.
4. **Mojibake apostrofo.** "L'uomo" (U+2019) → "LÆuomo": manca `NFC` + folding
   apostrofi/virgolette; rischia di far fallire `str.find` sullo span esatto
   ([segmentation.py:49-61](backend/app/pipeline/event_graph/segmentation.py)).

### Obiettivo (un intervento, tre parti)

- **A. Gate di ammissibilità dell'evento** — un nodo `:Evento` = **esattamente
  una forma verbale finita** (indicativo / congiuntivo / condizionale /
  imperativo) + i suoi argomenti. Nient'altro è un nodo.
- **B. Identità stabile della menzione** — stesso soggetto nominato N volte con la
  **stessa forma** = **un solo `:Menzione`**, id content-addressed stabile fra
  re-ingest e documenti.
- **C. Catene intrinseche al nodo** — `STESSO_EVENTO` / `AGGIORNA` /
  `CONTRADDICE` **non** sono archi che attraversano il grafo. Sono dato interno
  al nodo `:Evento` (riguardano il suo soggetto + quell'occorrenza precisa) e
  compaiono **solo** nella dashboard del nodo, ricostruiti in tempo reale con le
  altre occorrenze che vi partecipano. Il grafo mostra **solo** eventi: spina
  dorsale + tutto ciò che c'è intorno + i collegamenti fra eventi.

Ordine di esecuzione suggerito: **Fase 1** = A + B (estrazione + identità);
**Fase 2** = C (catene + visualizzazione). Indipendenti; C non dipende da A/B.

### Vincoli fissati dall'utente

- Predicati **non finiti liberi** (non retti da verbo aspettuale/modale/causativo):
  non sono nodi; si **salvano sempre nella factsheet** (`predicati_non_finiti`) e
  in **questo fix** si usano per costruire archi/circostanze fra eventi finiti.
- Frasi **gnomiche/morale** con verbo finito ("la gentilezza è più efficace")
  **restano** eventi. Nessun filtro semantico anti-morale.
- **Forme diverse dello stesso referente restano `:Menzione` distinte**
  (`l'uomo` ≠ `il viandante`): l'identità cross-forma si legge dagli **eventi che
  li collegano**, non fondendo i nodi. È il comportamento voluto, non un limite.

### Architettura da rispettare

- `event_graph/` non importa `app.core.*` / `app.pipeline.*` (D6). Nessuna nuova
  dipendenza, nessun POS-tagger: si opera **solo** sugli enum già restituiti
  dall'agente (D1/D2). Confine del determinismo: regole in Python puro sugli enum.
- Append-only, `MERGE` idempotente, provenienza (`regola`, `versione_regole`).
  Nessun `DELETE`. Il gate deve essere **idempotente** (rieseguirlo su una
  factsheet già filtrata = no-op): `_finalize_frase` lo chiama su ogni passata.

---

## PARTE A — Gate di ammissibilità dell'evento

### A0. Segnali già disponibili (nessun cambio allo schema di `EventoGrezzo`)

Su `EventoGrezzo` ([models/event_graph.py:108-134]):
`tempo ∈ {presente, imperfetto, passato, trapassato, futuro, non_finito}`,
`segmentazione ∈ {principale_finita, subordinata_finita, coordinata_finita,
infinitiva, gerundiva, participiale, nominale, implicita}`,
`frase_tipo ∈ {dichiarativa, interrogativa, imperativa}`, `completiva_di`,
`classe_verbo_reggente`, `modalita`, `lemma` (che **deve** essere l'infinito di
dizionario).

> Nota: `chunking_periods.ha_verbo_finito` NON è usabile a livello di span —
> testato falso-negativo su passato remoto breve (`disse`, `propose`, `rimase`).
> Serve solo a livello chunk.

### A1. Regole di finitezza (deterministiche, in Python sugli enum)

**A1a — gate hard.** Un `EventoGrezzo` è **finito** (⇒ candidabile a nodo) sse:

- `frase_tipo == "imperativa"` **oppure** `tempo != "non_finito"`; **e**
- `segmentazione ∈ {principale_finita, subordinata_finita, coordinata_finita}`.

**A1b — reject "parola nuda"** (violazione checklist, quindi con un giro di
correzione prima del drop). Anche se A1a passa, l'evento è spurio se:

- `_is_adverb_lemma(lemma)` (set esteso + `-mente` / `-ly`), **oppure**
- testo IT e `lemma` **non** matcha `/(are|ere|ire|rre|arsi|ersi|irsi)$/i`
  (non è un infinito italiano → è un nome/avverbio, o un lemma flesso per errore).
  → la correzione chiede "lemma = infinito di dizionario"; se resta invariato →
  `quarantena {motivo:"evento spurio"}`.
  (EN: nessun suffisso affidabile; si resta su A1a + `_is_adverb_lemma`. Rischio
  residuo dichiarato.)
- resta attivo `_span_inside_quotes` (drop interno virgolette).

**A1c — conflitto tra segnali.** Se `tempo`/`segmentazione` si contraddicono
(uno "finito", l'altro no) → si tratta come **non finito** (⇒ A2, poi A3). Meglio
parcheggiare che coniare un nodo dubbio.

### A2. Collasso del non finito **retto** (aspettuale / modale / causativo)

Per ogni evento non finito, trovare il **governatore** nella stessa `frase_indice`:

- via `completiva_di` (indice del reggente), **oppure**
- fallback: evento con `lemma ∈ _ASPECTUAL_LEMMAS` ([extraction.py:297-310]) o
  `classe_verbo_reggente != "nessuna"` o `modalita != "fattuale"`, il cui `span`
  finisce **prima** dello span del figlio, con testo interposto ≤ 15 char che
  matcha `/^\s*(a|ad|di|per|to)?\s*$/`.

Governatore aspettuale/fasale/modale/causativo ⇒ **collasso in UN evento finito**:

- si tiene il **figlio** (verbo lessicale) come nodo;
  `lemma` = lemma del figlio, passato per mini-mappa irregolari IT
  (`tolgere→togliere`, `arrendersi→arrendere`, …), fallback = lemma dato;
- `tempo` ← `tempo` del **governatore**; `frase_tipo` ← governatore;
- `SOGG` ← quello del figlio, altrimenti quello del governatore;
- `modalita` (regola precisa):
  - governatore `modalita == "volitivo"` (`voleva vincere` — anche se al passato:
    volizione non realizzata) → ereditata `volitivo` (⇒ `NON_FATTUALE`, spec §6);
  - governatore `modalita == "deontico"` **o** lemma `potere`, **in indicativo
    passato** (`tempo ∈ {passato, trapassato, imperfetto}`, `modalizzato_forma`
    non condizionale) → `modalita = "fattuale"` (modalità realizzata: "dovette
    arrendersi" / "poté partire" preservati come fatti — niente regressione sul
    climax);
  - governatore `modalita == "deontico"`/`potere` in **presente/futuro/
    condizionale** (`deve`, `dovrebbe`, `può`, `potrebbe`) → ereditata
    `deontico` (⇒ `NON_FATTUALE`);
  - governatore **aspettuale/fasale/causativo** (`cominciare`, `continuare`,
    `smettere`, `provocare`, `far sì che`…) → `modalita` del figlio invariata;
- `completiva_di` e archi grezzi verso il governatore → ripuntati al figlio;
  **governatore rimosso** dai `eventi`, registrato in `quarantena`
  `{motivo:"assorbito in <lemma figlio>"}` (audit).

**Infiniti coordinati** sotto lo stesso governatore ("cominciò a brillare e a
scaldare") → **un evento finito ciascuno**, tutti con lo stesso `tempo` ereditato.

Sostituisce il ramo aspettuale di `_prune_spurious_events` (righe 368-389).

### A3. Predicati non finiti **liberi** (nessun governatore)

Non diventano nodi. Vanno in `predicati_non_finiti` (A4) con: `lemma`, `span`,
`forma_verbale ∈ {infinito, gerundio, participio}`, `relazione_segnale`
(dall'agente, vocab §8.1), `governo_indice` (evento finito della stessa frase —
default: l'`e_testa`), `argomento_condiviso` (forma del SOGG/OGG condiviso).

**Uso a valle** (deterministico, nessun tipo d'arco nuovo) in
`event_edges.categorizza` / `sentence_pair_linking`, best-effort in quest'ordine:

1. `relazione_segnale` mappata da `RELAZIONE_TO_ARCO` / `COLLEGATO_RELAZIONI`
   ([event_edges.py:31-105]) **e** entrambi gli estremi risolvibili a eventi
   **finiti** (governo + evento finito adiacente o che condivide
   `argomento_condiviso`) → si emette quell'arco fra i due eventi finiti;
2. altrimenti → circostanza sul `governo_indice`: `MODO` (gerundi di maniera:
   "soffiando con violenza") o `OBL {preposizione}` (infiniti di scopo:
   "per proteggersi") — arco `(:Evento)->(:Menzione)`, nessun nodo evento;
3. altrimenti → resta **solo** nella factsheet JSON (auditabile, ri-processabile
   su bump `RULESET_VERSION`).

Per `sole-e-vento` la maggior parte sarà (2)/(3): l'acceptance è "0 nodi da
predicati liberi", non "N archi nuovi".

### A4. Modello — `models/event_graph.py`

- Nuovo `PredicatoNonFinito(BaseModel)`: `lemma`, `span`,
  `forma_verbale: Literal["infinito","gerundio","participio"]`,
  `relazione_segnale: RelazioneSegnale`, `governo_indice: int | None = None`,
  `argomento_condiviso: str | None = None`.
- `predicati_non_finiti: list[PredicatoNonFinito] = Field(default_factory=list)`
  su `FraseFactsheet` **e** `ChunkFactsheet` (default vuoto ⇒ JSON/test esistenti
  deserializzano senza modifiche).

### A5. Prompt — `extraction_prompts.py`

Riscrivere `_EVENT_DEFINITION`:

- "Un nodo evento È UNA SOLA ASSERZIONE: **esattamente un verbo di forma finita**
  (indicativo/congiuntivo/condizionale/imperativo) + i suoi argomenti. Un verbo
  non finito (infinito, gerundio, participio) da solo NON è MAI un evento."
- "`lemma` = **infinito di dizionario** (mai la forma flessa)."
- "cominciò a soffiare / voleva vincere / dovette arrendersi → **UN** evento sul
  verbo lessicale; `tempo` = quello del verbo finito reggente."
- "Clausole non finite libere (*per proteggersi*, *sentendo il vento*, *finito il
  lavoro*) NON sono eventi: in `predicati_non_finiti` con `relazione_segnale` e
  `governo_indice`."
- "Mai un evento il cui `span` è un avverbio nudo o un nome nudo."
- Rubrica bilingue: spunti IT/EN finito vs non-finito.
- `SYSTEM_FRASE_CORREZIONE` checklist: "+ ogni evento ha un verbo finito e
  `lemma` all'infinito; i predicati non finiti stanno in `predicati_non_finiti`".

### A6. Gate — `extraction.py`

- Nuova `_finite_gate(factsheet, testo) -> factsheet` (A1+A2+A3), **idempotente**,
  chiamata da `_finalize_frase` ([extraction.py:615-622]) al posto di
  `_prune_spurious_events` (di cui riusa i rami avverbi/virgolette).
- `evaluate_checklist`: nuove `ChecklistViolation(kind="evento",
  motivo=…)` per "predicato non finito" (senza governatore) e "lemma non
  infinito"/"evento spurio" → un giro di correzione, poi
  `_apply_remaining_violations` droppa in `quarantena`.

### A7. Threading

- `segmentation.py`: `SegmentationResult` ([segmentation.py:36-42]) porta
  `predicati_non_finiti`; `risolvi` / `risolvi_unita` li copiano dalla factsheet
  rimappando `governo_indice` → id evento risolto (meccanismo `indice_grezzo`,
  già usato per `completiva_di` in `factuality._index_parents`).
- `extraction._merge_frasi` ([extraction.py:748-782]): concatena
  `predicati_non_finiti` con remap degli indici.
- `dedup.espandi_zona_fino_dedup` ([dedup.py:57-111]): passa
  `seg.predicati_non_finiti` a `sotto` / al costruttore d'archi.
- `event_edges.categorizza` prende un nuovo parametro `predicati_non_finiti` +
  la mappa `menzioni` (per il match `argomento_condiviso`);
  `sentence_pair_linking` idem. Consumo come A3.
- **Persistenza**: `predicati_non_finiti` viaggia già dentro
  `:EgUnita.factsheet_json` (`persist_frase` serializza tutta la `FraseFactsheet`)
  — nessuna modifica a `persistence.py` / `schema.cypher` per questo.

---

## PARTE B — Identità stabile della menzione

### B1. `mention_coref.py`

- `_normalize_referential(forma) -> str`: `unicodedata.normalize("NFC", …)`,
  fold apostrofi/virgolette curve → dritte, `casefold`, rimozione di **un solo**
  determinante iniziale IT/EN (`il|lo|la|i|gli|le|l'|un|uno|una|the|a|an`),
  collasso spazi. NON tocca aggettivi/modificatori.
- `_fuse_referential_forms(sotto, eventi)` (generalizza `_fuse_proper_names`):
  - candidati = menzioni con `tipo_superficiale ∈ {nome_proprio, sn_comune}`;
  - clustering per **forma normalizzata uguale** OR contenimento token-span
    (riusa `_names_match` + `_cluster_proper_names`, [mention_coref.py:171-196]);
  - guardia `_compatibile`: mai unire con `numero`/`genere` in conflitto;
  - id canonico = `content_hash(forma_normalizzata)`; `non_risolto=False`;
  - `_retarget_args` + `_drop_redirected`.
- `risolvi_intra` ([mention_coref.py:325-341]): chiama `_fuse_referential_forms`
  al posto di `_fuse_proper_names`. `_risolvi_pronomi_e_nulli` **invariato**.
- `fondi_nomi_propri_vs_persistente` → `fondi_referenziali_vs_persistente`:
  `_PERSISTED_QUERY` ([mention_coref.py:28-31]) senza il filtro
  `tipo_superficiale = 'nome_proprio'`; match su forma normalizzata.

### B2. `ids.py`

`menzione_id`: per `sn_comune` con `forma_canonica` non vuota →
`content_hash(_normalize_referential(forma_canonica))`, `non_risolto=False`
(speculare a `nome_proprio`). Hash per-occorrenza **solo** per `pronome` /
`sogg_nullo` / forma vuota.
`_normalize_referential` va in un modulo importabile senza ciclo (nuovo
`event_graph/text_norm.py`, importato sia da `ids.py` sia da `mention_coref.py`).

### B3. Normalizzazione testo — `chunking_periods.py` + `segmentation.py`

- `preprocess_zona` / costruzione `UnitaTesto` ([chunking_periods.py:683+]):
  `NFC` + fold apostrofi/virgolette curve sul `testo` dell'unità **prima**
  dell'LLM e prima della ricerca span (usa `text_norm`).
- `segmentation._order_events` ([segmentation.py:49-61]): stesso fold sugli span
  prima di `testo.find`.

### B4. Persistenza — verifica (nessuna modifica attesa)

`persistence.persisti` fa `MERGE (:Menzione {id})`; con id stabili la dedup è
automatica. `schema.cypher`: `eg_menzione_id` unico + `eg_menzione_forma` già ok.

---

## PARTE C — Catene come dato interno al nodo, non archi del grafo

### C1. Modello — catene = proprietà del `:Evento`

`STESSO_EVENTO` / `AGGIORNA` / `CONTRADDICE` **cessano** di essere `:Relation` fra
`:Evento`. Diventano 4 proprietà sul nodo:

- `catena_id: str` — hash content-addressed della catena di coreferenza:
  `content_hash(f"{_lemma_norm(lemma)}|{sogg_menzione_id_canonico}")` (lemma
  identico + SOGG fuso = criterio candidati §10 in
  `event_coref._filtra_candidati_evento`). Stabile cross-ingestione senza archi.
- `catena_ruolo: "STESSO_EVENTO"|"AGGIORNA"|"CONTRADDICE"|null` — come **questa**
  occorrenza si rapporta alla precedente (null per la testa).
- `catena_precedente_id: str|null` — occorrenza precedente (ordine vecchio→nuovo).
- `catena_divergenze: list[str]` — campi divergenti (`["polarita"]`,
  `["fattualita"]`, `["argomenti"]`), da `event_coref._catena_tipo`
  ([event_coref.py:227-254]), per la spiegazione in dashboard.

### C2. `chains.py`

- `_applica_catena` (intra) e il ramo Catena di `applica_persistente` (cross-doc):
  **non** creano archi. Settano `catena_id` / `catena_ruolo` /
  `catena_precedente_id` / `catena_divergenze` sul nodo **nuovo** (append-only: il
  vecchio non si tocca; la catena si ricostruisce raggruppando per `catena_id`).
- `teste` / `biforcazioni`: diventano query per gruppo `catena_id` (testa =
  posizione minima; biforcazione = gruppo con ≥2 linee divergenti).
- **Fusione** (`fuso_in`) e **Successione** (no-op, tiene `SEQUENZA`) invariate.
- `event_coref.py` invariato: continua a produrre `EsitoCoref(kind="Catena",
  catena_tipo=…)`. Cambia solo la materializzazione.

### C3. `EventoRisolto` + `persistence.py` + `schema.cypher`

- 4 campi `catena_*` su `EventoRisolto`; `persisti` li scrive nel `MERGE
  (:Evento)`.
- `schema.cypher`: `CREATE INDEX eg_evento_catena IF NOT EXISTS FOR (e:Evento)
  ON (e.catena_id)` (additivo, `IF NOT EXISTS` — ammesso da D6).

### C4. Enum / catalog / query — rimozione dei tipi d'arco catena

- `TipoRelazione` ([models/event_graph.py:67-88]): togliere
  `STESSO_EVENTO`/`AGGIORNA`/`CONTRADDICE` dai **tipi d'arco**; restano come
  `CatenaTipo` (già in `event_coref`) per tipizzare `catena_ruolo`.
- `catalog.py` ([catalog.py:32,56-58,105]): la sezione `"catena"` passa da
  "archi" a **tratto del nodo** (finestra "Nodi e tratti", non legenda archi).
- `query_structured.py` ([query_structured.py:32-33]): `catena_di` da
  `MATCH path=(start)-[:STESSO_EVENTO|AGGIORNA|CONTRADDICE*1..8]-(e)` a
  `MATCH (s:Evento {id:$target}) MATCH (e:Evento {catena_id:s.catena_id})
  RETURN e ORDER BY e.posizione_doc, e.posizione_chunk`. Il filtro
  `tipo_relazione` rifiuta i 3 valori catena (o li reindirizza a `catena_di`).
- `GET /event-graph/graph` ([api/event_graph.py:263]): verificare che il payload
  archi non li richieda più (dopo la rimozione non esistono come archi).

### C5. Endpoint dashboard nodo — `GET /event-graph/nodo/{id}` ([api:281])

Aggiungere al payload:

```
"catena": {
  "catena_id": "...",
  "occorrenze": [ {id, ruolo, divergenze, posizione_doc, posizione_chunk,
                   ancora, tempo, fattualita, polarita, documento, sogg_forma}
                  ... ordinate vecchio→nuovo ]
}
```

Query: `MATCH (e:Evento {id:$id}) MATCH (o:Evento {catena_id:e.catena_id})
OPTIONAL MATCH (o)-[:SOGG]->(m:Menzione)
RETURN o, m.forma ORDER BY o.posizione_doc, o.posizione_chunk`.
"Tempo reale" = ricalcolata a ogni apertura; cresce con le ingestioni successive.

### C6. Frontend (`frontend/{components,lib}/event-graph/`)

- `lib/event-graph/encoding.ts` + `encoding.test.ts`: rimuovere lo styling archi
  catena (doppio tratto). Il grafo mostra solo `:Evento`/`:Menzione`, `SEQUENZA`,
  `SATELLITE_DI`, archi dizionario evento→evento, `COLLEGATO`, archi argomentali.
- `components/event-graph/ElementInspector.tsx`: nodo `:Evento` selezionato →
  sezione "Catena" (lista ordinata delle occorrenze, etichetta `ruolo` fra
  consecutive, `divergenze`), da `GET /event-graph/nodo/{id}`.
- `components/event-graph/EventLegend.tsx` + `lib/event-graph/legend.ts`: "catena"
  esce dalla legenda archi, entra in "Nodi e tratti".
- `lib/event-graph/types.ts` + `api.ts`: tipo `CatenaNodo` + campo nel payload.

### C7. `metrics.py`

`distribuzione_esiti_coref` e `densità CONTRADDICE` si calcolano da
`catena_ruolo` sui nodi invece che dai tipi d'arco.

---

## Acceptance criteria

### AC-A — gate evento (unit, `test_event_graph_finite_gate.py`, FakeSession + factsheet stub)

| # | Input factsheet | Output atteso |
|---|---|---|
| A1 | `soffiare {tempo:non_finito, seg:infinitiva}` sotto `cominciare {tempo:passato, seg:principale_finita}` | 1 evento `lemma=="soffiare"`, `tempo=="passato"`; `cominciare` assente + in quarantena `motivo` "assorbito…" |
| A2 | `brillare` + `scaldare` (non_finito) coord. sotto `cominciare {passato}` | 2 eventi, entrambi `tempo=="passato"` |
| A3 | `volere {passato} → vincere {non_finito}` | 1 evento `vincere`, `modalita=="volitivo"`, a valle `fattualita=="NON_FATTUALE"` |
| A4 | `dovere {passato} → arrendere {non_finito}` | 1 evento `arrendere`, `tempo=="passato"`, `modalita=="fattuale"`, `fattualita=="FATTUALE"` |
| A5 | `sentendo {non_finito, seg:gerundiva, completiva_di:null}`, nessun aspettuale in frase | 0 eventi da esso; 1 voce `predicati_non_finiti` con `governo_indice` = e_testa frase |
| A6 | `lentamente {lemma:"lentamente"}` | 0 eventi; quarantena `motivo` "evento spurio" |
| A7 | `essere {tempo:presente, seg:principale_finita, span:"è più efficace della forza"}` | 1 evento (gnomico tenuto) |
| A8 | conflitto `{tempo:passato, seg:gerundiva}` | 0 eventi (non coniato) |
| A9 | `tolgere {non_finito}` sotto aspettuale | evento `lemma=="togliere"` |
| A10 | `lemma` flesso per errore (`"disse"`) su evento altrimenti valido | violazione "lemma non infinito" → dopo correzione stub invariata → quarantena |
| A11 | factsheet già filtrata ripassata in `_finite_gate` | invariata (idempotenza) |

### AC-B — identità menzione (unit, `test_event_graph_mention_identity.py`)

| # | Input | Output atteso |
|---|---|---|
| B1 | 3 menzioni `{"il Sole"(sn_comune), "Sole"(nome_proprio), "il sole"(sn_comune)}`, stesso numero/genere | 1 `MenzioneRisolta`, `id == content_hash("sole")`, 3 argomenti ripuntati |
| B2 | `{"il Sole"(sing,masc)}` vs `{"i Soli"(plur,masc)}` | non fusi (2 nodi) |
| B3 | pronome "lo" con 2 antecedenti compatibili | invariato: `non_risolto==True` |
| B4 | `menzione_id("il Sole","sn_comune",…)` chiamato 2× | stesso id, `non_risolto==False` |
| B5 | span "sentendo il vento" con apostrofo curvo U+2019 nel testo | span trovato da `find` dopo il fold |
| B6 | `l'uomo` (sn_comune) vs `il viandante` (sn_comune) | **2 nodi distinti** (comportamento voluto) |

### AC-C — catene intrinseche (unit, `test_event_graph_chains_intrinseche.py`)

| # | Input | Output atteso |
|---|---|---|
| C1 | `chains.applica(sotto, ev, EsitoCoref(kind="Catena", catena_tipo="AGGIORNA", nuovo_id, candidato_id))` | `sotto.archi` **non** contiene `AGGIORNA`; `ev.catena_id == candidato.catena_id`, `ev.catena_ruolo=="AGGIORNA"`, `ev.catena_precedente_id==candidato.id`, `ev.catena_divergenze==["argomenti"]` |
| C2 | esito `STESSO_EVENTO` (nessuna divergenza) | `catena_ruolo=="STESSO_EVENTO"`, `catena_divergenze==[]` |
| C3 | esito `CONTRADDICE` (polarità diverge) | `catena_ruolo=="CONTRADDICE"`, `catena_divergenze==["polarita"]` |
| C4 | 2 occorrenze `soffiare`+SOGG `content_hash("vento")` in doc diversi | stesso `catena_id`, **0** archi catena |
| C5 | `applica_persistente` ramo Catena | `SET new.catena_* = …`; nessun `MERGE ()-[:AGGIORNA]->()`; nodo `old` invariato |
| C6 | `teste(sotto)` con una catena a 3 | ritorna solo l'occorrenza a posizione minima del gruppo |

### AC-E2E — re-ingest reale `sole-e-vento`

Prereq: Neo4j attivo (`docker compose up -d neo4j`) + backend `uvicorn app.main:app
--port 8000` + LLM locale (`OPENAI_BASE_URL` in `.env`). Gli helper leggono la
connessione da `backend/_e2e_env.py` (env var > `.env` > default); override porta
backend con `EG_BASE`.

`cd backend && python _wipe_eg.py && python _ingest_and_wait.py && python _dump_sole_vento.py`
poi assert Cypher (documento `sole-e-vento`, `fuso_in` nullo):

| # | Query | Atteso |
|---|---|---|
| E1 | `MATCH (e:Evento) WHERE e.tempo='non_finito' RETURN count(e)` | **0** |
| E2 | `… WHERE e.segmentazione IN ['infinitiva','gerundiva','participiale','nominale','implicita']` | **0** |
| E3 | `… WHERE e.lemma =~ '(?i).*mente$' OR e.lemma IN ['lentamente','facilmente','slowly','easily']` | **0** |
| E4 | `… WHERE NOT e.lemma =~ '(?i).*(are|ere|ire|rre|arsi|ersi|irsi)$'` | **0** (tutti i lemma sono infiniti IT) |
| E5 | `MATCH (e:Evento) RETURN count(e)` | **28–42** (era 56) |
| E6 | evento per `si tolse il mantello` | 1 nodo, `lemma∈['togliere']`, `tempo='passato'`, `fattualita='FATTUALE'` |
| E7 | evento per `dovette arrendersi` | 1 nodo, `tempo='passato'`, `fattualita='FATTUALE'` |
| E8 | `MATCH (m:Menzione) WHERE toLower(m.forma_canonica) CONTAINS 'sole'` + `(:Evento)-[:SOGG]->(m)` | un **solo** id menzione, SOGG di ≥6 eventi |
| E9 | idem `'vento'`, `'mantello'` | un solo nodo ciascuno |
| E10 | `MATCH (m:Menzione) RETURN count(m)` | **≤ 30** (era 54) |
| E11 | re-ingest 2× → `count(:Evento)`, `count(:Menzione)` | **identici** (idempotenza) |
| E12 | `MATCH (q:Quarantena)` | `rispose`, `imparò` NON in quarantena (sono eventi finiti con SOGG) |
| E13 | `MATCH ()-[r:STESSO_EVENTO\|AGGIORNA\|CONTRADDICE]->() RETURN count(r)` | **0** (catene non sono archi) |
| E14 | `MATCH (e:Evento) WHERE e.catena_id IS NOT NULL` | `soffiare` sul Vento = un solo `catena_id` con ≥2 occorrenze |
| E15 | `GET /event-graph/graph?documento=sole-e-vento` | nessun arco di tipo catena nel payload |
| E16 | `GET /event-graph/nodo/{id di un soffiare ricorrente}` | `catena.occorrenze` len ≥2, ordinate, `ruolo` valorizzato sulle non-teste |

### AC-REG — nessuna regressione

`cd backend && python -m pytest tests/test_event_graph_*.py -q` verde. Attese
aggiornate **deliberatamente** (diff rivisto) in:
`test_event_graph_m_micro1.py`, `test_event_graph_m_dedup.py`,
`test_event_graph_m3a0.py`, `test_event_graph_m3.py`, `test_event_graph_m9.py`
(catene ora proprietà), `test_acceptance_event_graph_e2e.py`,
`test_event_graph_m_eval.py`, e gold in
`backend/tests/fixtures/_build_eval_corpus.py` +
`event_graph_eval_corpus.json`. Frontend: `frontend/lib/event-graph/encoding.test.ts`,
`legend.test.ts` (voce "catena" spostata).

---

## Documentazione

`PIANO-GRAFO-EVENTI.md` — **Addendum 3 (2026-09-08)**:

- "evento = una sola forma verbale finita". Sovrascrive Addendum 1
  ("stati/descrizioni sono eventi" → solo se finiti) e i label §3
  `{infinitiva, gerundiva, participiale, nominale, implicita}` (ora →
  `predicati_non_finiti`, mai nodi).
- Gate identità menzione (fusione per forma normalizzata, id content-addressed);
  scelta "forme diverse = menzioni diverse, identità dagli eventi che le
  collegano".
- **Catene intrinseche**: la famiglia "Catena" **esce** dalla tabella archi §6
  (diventa tratto del nodo: `catena_id` / `catena_ruolo` /
  `catena_precedente_id` / `catena_divergenze`); aggiorna §11.2-11.3 (teste /
  biforcazioni / query cronologia), §15.3 (encoding: niente archi catena),
  §15.5 (legenda: "catena" in "Nodi e tratti"), §16 (payload `GET /nodo/{id}`
  con `catena.occorrenze`).

---

## Fuori scope (segnalati)

- Rimozione codice morto `narrative_plane.py` / `backbone.py`.
- `pipeline.py` `llm_calls` / `reused_factsheets` hard-coded `0` (metriche costo).
- Estrazione **profonda** dei dialoghi (Addendum 1 "rinviata"): qui si garantisce
  solo che l'evento della reggente (`disse`, `marca_dialogo=true`) superi il gate.
- `PRECEDE {base:"dato_esplicito"}` troppo generoso + placeholder
  `COLLEGATO {ordine_ingestione}` ~50% — fix separato del livello temporale.
- File scratch `backend/_*.py` / `_*.out.txt` → `.gitignore`.

## Verifica end-to-end (ordine)

**Fase 1 (A+B):**
1. `pytest tests/test_event_graph_finite_gate.py tests/test_event_graph_mention_identity.py -q` → AC-A, AC-B.
2. `pytest tests/test_event_graph_*.py -q` → AC-REG.
3. `python _wipe_eg.py && python _ingest_and_wait.py && python _dump_sole_vento.py` → AC-E2E E1–E12.

**Fase 2 (C):**
4. `pytest tests/test_event_graph_chains_intrinseche.py tests/test_event_graph_m9.py -q` → AC-C.
5. `cd ../frontend && npm test -- event-graph` → encoding/legend/inspector.
6. Re-ingest + `_dump_sole_vento.py` + `GET /event-graph/nodo/{id}` → AC-E2E E13–E16.
7. `graphify update .`.
