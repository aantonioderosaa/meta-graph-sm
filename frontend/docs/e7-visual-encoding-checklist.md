# Checklist visuale Graph Explorer — Epic 7 (§11.1) + F2.3 + V1.3

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
| due `toNvlGraph` identici | stessa identità oggetto per ogni nodo/arco (F2.2) | sì |
| solo `pulsing` cambia su un id | nuovo oggetto solo per quel nodo (F2.2) | sì |
| sottoinsieme di id in input | cache senza entry orfane (F2.2) | sì |
| `has_history=true` su fatto corrente | caption con prefisso `●`, colore type invariato (V1.2/V1.3) | sì (`fact-latest`, `pref-with-history`) |
| `has_history=true` × `is_latest=false` | `●` + `(storico)` + opacità ridotta, senza sostituire né l’uno né l’altro (V1.3) | sì (`ep-historical-with-history`) |
| `has_history=false` / assente | nessun prefisso `●` | sì |
| `has_history` × pulse / selezione | marker `●` resta; size/selected restano governati da pulse/selezione | sì |

Manuale UI (su dati reali dopo ingest+dreaming):

- [ ] Toggle **Solo correnti** nasconde nodi con stile storico
- [ ] Click nodo apre pannello dettaglio con provenienza
- [ ] Doppio click su testa di catena evidenzia solo la catena `UPDATES`
- [ ] Durante ingest con raffica eventi, pulse ~600ms senza scatti percepibili (F2.1)
- [ ] Con **Solo correnti** attivo, un fatto con `UPDATES` uscente mostra `●` in caption; uno senza non lo mostra (V1.2)
