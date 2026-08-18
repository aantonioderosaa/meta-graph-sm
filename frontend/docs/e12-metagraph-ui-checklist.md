# Checklist UI Metagraph layer — Fase 12 / placeholder Fase 14.5

Dataset: grafo dopo ingest + dreaming con backbone, faccette, CONTRADICTS, ConnectivityRule e almeno un `:JudgeRun`. I pannelli sono **liste/alberi** nella tab laterale (nessun canvas NVL extra; `EntityEventExplorer` resta l’unico grafo).

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
