# Checklist visuale Graph Explorer — Epic 7 (§11.1)

Dataset di riferimento: `frontend/lib/graph-visual-fixture.ts` (usato dai test di encoding; **non** importato dal Graph Explorer dopo lo swap E7.6).

| Caso | Encoding atteso | Verificato in test |
|------|-----------------|--------------------|
| `type=fact`, `is_latest=true` | cerchio teal `#0F766E` | sì (`graph-encoding.test.ts`) |
| `type=preference`, `is_latest=true` | cerchio amber `#B45309` | sì |
| `type=episode`, `is_latest=true` | cerchio slate `#475569` | sì |
| qualsiasi type, `is_latest=false` | opacità ridotta + caption `(storico)` | sì |
| relazione `UPDATES` | colore warning `#D97706`, freccia più spessa | sì |
| relazione `EXTENDS` | colore info `#2563EB` | sì |
| relazione `DERIVES` | colore success `#16A34A`, stroke più sottile (proxy del tratteggiato; NVL non espone dash nativo) | sì |
| due fatti complementari con `EXTENDS` | entrambi correnti, arco EXTENDS visibile insieme | sì (fixture `pref-latest`↔`fact-latest`) |
| nessun nodo `Chunk` | filtrati lato BE + FE | sì |

Manuale UI (su dati reali dopo ingest+dreaming):

- [ ] Toggle **Solo correnti** nasconde nodi con stile storico
- [ ] Click nodo apre pannello dettaglio con provenienza
- [ ] Doppio click su testa di catena evidenzia solo la catena `UPDATES`
