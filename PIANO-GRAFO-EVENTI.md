# Piano — Grafo degli eventi

> Il piano originale (M1–M23, Addendum 1–2, 2026-09-07) **non è versionato in
> questo repo**. Copia di lavoro: `../implementation_plans_done/PIANO-GRAFO-EVENTI.md`.
> Norma operativa: i moduli in `backend/app/pipeline/event_graph/` e gli addenda
> sotto. Le sezioni citate (§3, §6, §11.2–11.3, §15.3, §15.5, §16) sono quelle
> di quel piano; questo file non le riscrive.

---

## Addendum 3 (2026-09-08): gate finito + identità menzione + catene intrinseche

Tre decisioni che **sovrascrivono** parti del piano originale e degli Addendum 1–2.
Implementate in `PIANO-FIX-EVENTO-ASSERZIONI.md` (Parti A, B, C). Non toccano
l'isolamento D6 né l'append-only.

---

### 1. Evento = una sola forma verbale finita

**Sovrascrive** Addendum 1 («Decisione — cosa è un evento»):

| Addendum 1 (2026-09-07) | Questo addendum |
|---|---|
| Criterio: «qualcosa che è successo», quasi-atomico; «non è più un verbo finito = un nodo» | Criterio: **esattamente una forma verbale finita** (indicativo / congiuntivo / condizionale / imperativo) + i suoi argomenti. Nient'altro è un nodo `:Evento`. |
| Stati / descrizioni (`indossava un mantello`, `era stanco`, `la porta era chiusa`) **sono eventi come gli altri** | Lo restano **solo se finiti** (verbo finito nella riga). Una descrizione nominale / participiale / infinitiva **non** conia un nodo. |
| Modali / ipotetici estratti comunque, marcati `NON_FATTUALE` / `IPOTETICO` | Invariato, **purché** il nodo sia il verbo lessicale collassato sotto un governatore finito (A2) oppure sia già finito. |

**Sovrascrive** i label §3 di `segmentazione`
`{infinitiva, gerundiva, participiale, nominale, implicita}`: non sono più
candidabili a nodo. Vanno in `predicati_non_finiti` sulla factsheet, **mai**
`:Evento`. Restano nodi solo
`{principale_finita, subordinata_finita, coordinata_finita}`.

Gate deterministico (`extraction._finite_gate`, chiamato da `_finalize_frase`):

- **A1a** — finito sse `frase_tipo == "imperativa"` **oppure** `tempo != "non_finito"`,
  **e** `segmentazione` in `{principale_finita, subordinata_finita, coordinata_finita}`.
- **A1b** — anche se A1a passa: avverbio nudo / lemma IT che non è infinito di
  dizionario → correzione, poi `quarantena {motivo:"evento spurio"}`.
- **A1c** — `tempo` e `segmentazione` in conflitto → trattato come non finito.
- **A2** — non finito **retto** da aspettuale/modale/causativo → collasso in un
  evento finito sul figlio (lemma lessicale, `tempo` del governatore).
- **A3** — non finito **libero** → `predicati_non_finiti` (uso a valle per
  archi/circostanze fra eventi finiti, o solo factsheet).

Frasi gnomiche/morale con verbo finito (`la gentilezza è più efficace`) **restano**
eventi. Nessun filtro semantico anti-morale. Discorso diretto: come Addendum 1,
solo l'atto di dire (`disse`) deve superare il gate; estrazione profonda del
dialogo resta rinviata.

---

### 2. Gate identità della menzione

**Sovrascrive** sez. 6 «Identità derivata dal contenuto» (id per-occorrenza per
i non-propri) e sez. 9 / D4 (`mention_coref`: fusione solo nome proprio, stringa
identica / sottostringa).

- Fusione per **forma normalizzata** (`text_norm._normalize_referential`: NFC,
  fold apostrofi/virgolette, `casefold`, un determinante iniziale IT/EN,
  collasso spazi). Candidati: `tipo_superficiale ∈ {nome_proprio, sn_comune}`.
  Guardia: mai unire con `numero`/`genere` in conflitto.
- Id **content-addressed**: `ids.menzione_id` per `sn_comune` con
  `forma_canonica` non vuota = `content_hash(forma_normalizzata)`,
  `non_risolto=False` (speculare al nome proprio). Hash per-occorrenza **solo**
  per `pronome` / `sogg_nullo` / forma vuota.
- Materializzazione: `mention_coref._fuse_referential_forms` (intra) e
  `fondi_referenziali_vs_persistente` (cross-doc). Stesso soggetto nominato N
  volte con la **stessa forma** = **un solo** `:Menzione`.

**Scelta voluta, non un limite:** forme di superficie diverse = menzioni
distinte. L'identità cross-forma si legge dagli **eventi che le collegano**,
non fondendo i nodi. `l'uomo` ≠ `il viandante`.

---

### 3. Catene intrinseche al nodo (non archi)

**Sovrascrive** sez. 6 tabella Archi, famiglia «Catena»
(`STESSO_EVENTO`, `AGGIORNA`, `CONTRADDICE` come tipi d'arco, direzione
vecchio→nuovo). La famiglia **esce** dalla tabella. Diventa **tratto del nodo**
`:Evento`:

| Proprietà | Ruolo |
|---|---|
| `catena_id` | hash content-addressed `lemma\|sogg_menzione_id` (stabile cross-ingestione senza archi) |
| `catena_ruolo` | `"STESSO_EVENTO"` \| `"AGGIORNA"` \| `"CONTRADDICE"` \| `null` (null = testa) |
| `catena_precedente_id` | occorrenza precedente (ordine vecchio→nuovo) |
| `catena_divergenze` | campi divergenti (`polarita`, `fattualita`, `argomenti`) |

`event_coref.py` resta invariato: produce ancora `EsitoCoref(kind="Catena",
catena_tipo=…)`. Cambia solo la materializzazione in `chains.applica` /
`applica_persistente`: **nessun** `MERGE ()-[:AGGIORNA]->()`; si settano i
quattro campi sul nodo **nuovo** (append-only: il vecchio non si tocca).
Fusione (`fuso_in`) e Successione (`SEQUENZA`) invariate.

Il grafo mostra **solo** eventi: spina + dintorni + collegamenti fra eventi
(`SEQUENZA`, `SATELLITE_DI`, dizionario evento→evento, `COLLEGATO`, argomentali).
Le catene si vedono nella dashboard del nodo, ricostruite in tempo reale.

#### §11.2–11.3 (teste / biforcazioni / query cronologia)

Sovrascrive sez. 10 (`chains.py` §11) e i punti di sez. 11.2 che trattano
`AGGIORNA` come arco a lunga distanza:

- **Testa** = occorrenza a posizione minima del gruppo `catena_id` (non più:
  «evento senza arco catena uscente»).
- **Biforcazione** = gruppo `catena_id` con ≥2 linee divergenti (non più:
  «≥2 archi catena entranti»).
- **Query cronologia** (sez. 11.3): invariata su `SEQUENZA` + `PRECEDE`
  non-superati. La catena **non** è un cammino di archi; la cronologia delle
  occorrenze di un evento si ottiene con
  `MATCH (e:Evento {catena_id: $id}) RETURN e ORDER BY e.posizione_doc,
  e.posizione_chunk`. `query_structured.catena_di` usa questa query; il filtro
  `tipo_relazione` rifiuta (o reindirizza a `catena_di`) i tre valori catena.

Sez. 11.2 punto 4 («nuovo che aggiorna un vecchio → `AGGIORNA` a lunga
distanza»): l'esito Catena cross-doc resta, ma si materializza sui campi
`catena_*` del nodo nuovo, non come arco. Sez. 13 (congelato vs affina): le
catene restano congelate; non ci sono più archi catena da non riscrivere.

#### §15.3 Encoding

Niente styling archi catena (doppio tratto). Il grafo mostra `:Evento` /
`:Menzione`, `SEQUENZA`, `SATELLITE_DI`, archi dizionario evento→evento,
`COLLEGATO`, archi argomentali. Isolare una catena in UI = filtrare i nodi per
`catena_id`, non nascondere una famiglia d'archi.

#### §15.5 Legenda

«Catena» **esce** dal catalogo archi (sempre visibile) ed **entra** in «Nodi e
tratti» (`catalog.py`, sezione `"catena"` = tratto del nodo). Vocabolari chiusi
dei tratti: si aggiungono `catena_id` / `catena_ruolo`.

#### §16 Payload `GET /event-graph/nodo/{id}`

Endpoint non elencato nella tabella originale sez. 16; è il punto di lettura
della catena. Payload aggiuntivo:

```
"catena": {
  "catena_id": "...",
  "occorrenze": [ {id, ruolo, divergenze, posizione_doc, posizione_chunk,
                   ancora, tempo, fattualita, polarita, documento, sogg_forma}
                  ... ordinate vecchio→nuovo ]
}
```

Query: `MATCH (e:Evento {id:$id}) MATCH (o:Evento {catena_id:e.catena_id})
OPTIONAL MATCH (o)-[:SOGG]->(m:Menzione) RETURN o, m.forma ORDER BY
o.posizione_doc, o.posizione_chunk`. Ricalcolata a ogni apertura; cresce con
le ingestioni successive.

`GET /event-graph/graph`: il payload archi non include tipi catena.

Metriche (sez. 19 / spec §16): `distribuzione_esiti_coref` e densità
`CONTRADDICE` si calcolano da `catena_ruolo` sui nodi, non dai tipi d'arco.

---

### Moduli che implementano questo addendum

| Decisione | Modulo |
|---|---|
| Gate finito (A1–A3) | `extraction._finite_gate` |
| Normalizzazione testo / forma | `text_norm` (`_normalize_referential`; NFC + fold su chunk/span) |
| Fusione menzioni per forma | `mention_coref._fuse_referential_forms` |
| Id content-addressed menzione | `ids.menzione_id` |
| Materializzazione catene | `chains.applica` / `applica_persistente` |
| Dashboard nodo | `GET /event-graph/nodo/{id}` (`api/event_graph.py`) |
