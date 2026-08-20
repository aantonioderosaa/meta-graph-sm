# Checklist UI Metagraph layer — successore di e10-manual-ui-checklist (Fase 14.5)

Dataset: grafo dopo ingest + dreaming con backbone, faccette, CONTRADICTS, ConnectivityRule e almeno un `:JudgeRun`. I pannelli sono **liste/alberi** nella tab laterale (nessun canvas NVL extra; `EntityEventExplorer` resta l’unico grafo). Automated vs manual: la tabella sotto è la superficie già coperta dai test; i casi §13 sono verifica umana sui pannelli di Fase 12.

| Caso | Superficie attesa | Verificato in test |
|------|-------------------|--------------------|
| Albero dominio `IS_A` / `MEMBER_OF` | tab **Dominio** (`ConceptDomainExplorer`), espansione ricorsiva, `kernel_category` / `definition` | sì (`concept-tree.test.ts`) |
| Badge faccette sul grafo | caption ` · faccette` se `has_facets` o `facet_count > 1` | sì (`graph-encoding.test.ts`) |
| Stacco faccetta | tab **Identità**, pulsante **Stacca faccetta** → `POST /graph/identities/{uri}/unlink` (non cancella `:Node`) | sì (path API `metagraph-api.test.ts`) |
| CONTRADICTS aperti | tab **Contraddizioni**, elenco filtrabile, mai auto-nascosto; click evidenzia entrambi gli id | sì (path API + copy UI) |
| Regole S1 | tab **Regole**, triple + `origin_count` | sì (path API) |
| Log giudice | tab **Giudice**, conteggi per compito da `GET /graph/judge-runs` | sì (path API) |
| Badge epistemici | Query: **ASSERITO** (muted) / **DERIVATO** (warning); DERIVATO espande `derivation_chain` | sì (`citation-badges.test.ts`) |
| Un solo grafo | `DashboardShell` monta esattamente un `EntityEventExplorer` | sì (`test_acceptance_solo_entita_eventi.py`) |

Manuale UI (su dati reali):

- [ ] Tab **Dominio**: i catch-all kernel sono radici; espandere un genere mostra figli `IS_A` e membri `MEMBER_OF`; click evidenzia il concetto nel grafo
- [ ] Nodo con identità multi-faccetta: caption con ` · faccette` nei pannelli entità
- [ ] Tab **Identità**: elenco uri + faccette; **Stacca faccetta** chiede conferma e precisa che il nodo non viene eliminato; dopo lo stacco il nodo resta nel grafo
- [ ] Tab **Contraddizioni**: una coppia aperta resta visibile anche con filtro vuoto; il filtro restringe la lista, non cancella i CONTRADICTS
- [ ] Tab **Regole**: una regola S1 mostra livello di generalizzazione e numero di origini
- [ ] Tab **Giudice**: l’ultima passata è in cima; i sei conteggi coincidono con il `:JudgeRun` in Neo4j
- [ ] Query NL: citazione asserita → badge ASSERITO; salto derivato → DERIVATO cliccabile con passi `s0`/`s1`
- [ ] Mobile: pulsanti Pipeline / Query / **Layer**; Layer apre le stesse tab del sidebar desktop

## Fase 15 — vista generale (nomi soli, un clic ai metadati)

Sostituisce l’interazione di Fase 15 (canvas piatto + solo metadati). Toggle **Vista dettagliata** (default) ↔ **Vista generale**. La vista generale monta solo `MacroGraphPanel` (un canvas NVL). Le schede Fascio / Metadati sono liste, non canvas. Non montare mai MacroGraphPanel e EntityEventExplorer insieme (cap WebGL). **Fase 17 ha sostituito questa interazione** (dashboard + grafo a scope); i test e il canvas montato sono quelli della sezione Fase 17 sotto.

| Caso | Superficie attesa | Verificato in test |
|------|-------------------|--------------------|
| Solo nomi sul canvas | caption = nome nodo; archi caption = `relation_count` (`"3"`), type `BUNDLE` | sì (`test_macro_graph.py`) |
| Click arco → fascio | `GET /graph/bundle/{a}/{b}`, elenco relazioni, badge ASSERITO | sì (path API + `bundle-detail.test.ts`) |
| Click nodo → metadati | `GET /graph/metadata/{id}` (breadcrumb `IS_A` / sommario / faccette via `IdentityDetailPanel`) | sì (`test_macro_graph.py`, path API) |
| Colori kernel | `colorByKernelCategory`; `encodeNode` per type resta invariato | sì (`graph-encoding.test.ts`) |
| Toggle senza 5° canvas | default dettagliata; `{generale ? Macro : EntityEventExplorer}` | sì (`test_acceptance_solo_entita_eventi.py`) |

Manuale UI — Fase 15:

- [ ] Default all’apertura: **Vista dettagliata**, quattro pannelli Entità/Concetti/Eventi/Partecipazione come in Fase 12; tab laterali invariate
- [ ] Toggle **Vista generale**: un solo grafo, caption solo nomi (niente `kernel_parent` / testimoni / definizione sul canvas); gli archi mostrano un numero
- [ ] Click su un arco: il pannello Fascio elenca le relazioni individuali con badge **ASSERITO**; espandere mostra `kernel_parent`, testimoni, `valid_time`
- [ ] Click su un nodo: Metadati (definizione/breadcrumb per un concetto; sommario/attributi per un `:Node`); le faccette riusano il pannello Identità
- [ ] Tornando a **Vista dettagliata** i quattro pannelli Fase 12 si comportano come prima; MacroGraphPanel è smontato (niente secondo grafo NVL)

## Fase 17 — dashboard sottodomini + grafo a scope annidato

Sostituisce l’interazione di Fase 15 (canvas piatto + solo metadati). Toggle **Vista dettagliata** (default) ↔ **Vista generale**. La vista generale monta `DomainDashboard` (lista, non canvas) + `DomainDetailCard` (scheda, non modale) + `DomainGraphPanel` (un canvas NVL via `GraphPanel`). Non montare mai DomainGraphPanel e EntityEventExplorer insieme (cap WebGL). `GET /graph/macro` resta nel backend ma non è più la vista primaria.

| Caso | Superficie attesa | Verificato in test |
|------|-------------------|--------------------|
| Lista completa dei domini | `GET /graph/domains` — promossi e catch-all, nessun `LIMIT` silenzioso | sì (`test_domain_dashboard.py`) |
| Dizionario Σ_D + regole scoped | `GET /graph/domains/{id}/dictionary` e `/rules`; un clic dalla dashboard | sì (`test_domain_dashboard.py`, path API vitest) |
| Figli diretti ricorsivi | Concept → seleziona il Concept; Node → `NodeMetadataPanel` (`node_type` Entità/Evento) | sì (path API + `domain-nav.test` via `domain-dashboard-api.test.ts`) |
| Grafo radice senza `:Node` sciolti | `GET /graph/domains-graph` solo `:Concept` con almeno un `BUNDLE` | sì (`test_domain_dashboard.py`) |
| Drill + freccia Indietro | stack `drillPath` in `DashboardShell`; `GET /graph/domains/{id}/children-graph`; pop = un click | sì (`domain-dashboard-api.test.ts`, `test_acceptance_solo_entita_eventi.py`) |
| Lista MEMBER_OF testuale | nello scope, colonna «Dentro questo dominio» ridondante col grafo | sì (componente `DomainGraphPanel`) |
| Toggle senza 5° canvas | default dettagliata; `{generale ? Domain* : EntityEventExplorer}` | sì (`test_acceptance_solo_entita_eventi.py`) |

Manuale UI — Fase 17:

- [ ] Default all’apertura: **Vista dettagliata**; tab laterali invariate
- [ ] Toggle **Vista generale**: lista scrollabile di tutti i Concept; un clic su una riga apre dizionario e regole nella scheda (non un modale)
- [ ] Dalla scheda si scende ai figli Concept fino alle foglie Node; ogni foglia apre i metadati (etichetta Entità o Evento)
- [ ] Il grafo in radice mostra solo Concept legati da fasci (numeri in caption); nessun nodo foglia accanto ai Concept
- [ ] Click su un Concept nel grafo sostituisce il canvas con i figli diretti; **Indietro** (sopra il canvas) torna allo scope precedente
- [ ] Nello scope, la lista testuale elenca i `:Node` MEMBER_OF; click su un arco apre ancora il pannello Fascio
- [ ] Tornando a **Vista dettagliata**, DomainGraphPanel è smontato (niente secondo grafo NVL)

Manuale UI — sei casi-limite del §13 (Doc1), come li vede un umano nei pannelli Fase 12:

- [ ] **Ditta individuale (Agente + CostruttoSociale)**: tab **Identità** mostra **due faccette** sullo stesso `:IdentityNode` (persona / ditta), non un unico nodo con due `kernel_category`. Tab **Dominio**: filtro/query per Persona evidenzia solo la faccetta Agente; Organizzazione solo CostruttoSociale. **Stacca faccetta** non cancella i `:Node`
- [ ] **Relazione che sembra un fratello orizzontale** (`coached_by` o analogo): tab **Dominio** non ha un tipo orfano accanto a R1–R6; il fatto resta raffinamento verticale (catch-all / padre `kernel_parent` Partecipativa). Nessun nuovo tipo in albero
- [ ] **Raffinamenti equivalenti da domini sorelli**: tab **Giudice** registra `EQUIVALENT_TO`; i membri restano nel grafo con provenienza `absorbed_from`; **Dominio** non introduce un nono genere kernel
- [ ] **Referente cross-dominio con relazioni contraddittorie apparenti**: tab **Identità** due faccette; gli archi di ciascuna restano visibili (plays_for vs president_of). **Contraddizioni** non “risolve” fondendo i nodi
- [ ] **Contraddizione tra fonti in ingestione**: tab **Contraddizioni** mostra la coppia e **non** la nasconde; entrambe le asserzioni restano `is_latest` (nessuna scomparsa da Query / grafo). PROMOTE non ha ritipizzato CONTRADICTS
- [ ] **Collegamento cross-dominio mai attestato**: tab **Query** badge **DERIVATO** sul salto non asserito, con catena `s0`/`s1`; il grafo NVL non mostra un `:Relation` inventato. Tab **Regole**: se nessuna regola S1 autorizza, nessun candidato / nessuna riga DERIVATO fittizia
