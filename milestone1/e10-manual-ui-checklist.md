# Checklist manuale UI — Milestone 1 §8 (E10.2)

Percorso da eseguire sull'app completa (`docker compose up` o avvio locale).
Compilare con ✓/data/note. Allegare screenshot o registrazione dove indicato.

Prerequisiti: `OPENAI_API_KEY` in `.env`, `NEXT_PUBLIC_USE_MOCK_EVENTS=false`.

| # | Criterio | Passi UI | Evidenza | OK |
|---|----------|----------|----------|----|
| 1 | Ingest → chunks+facts, rumore scartato | Ingest di un testo con 1 fatto + riga "ok capito noise"; osservare Pipeline Monitor (chunking/extraction); Graph Explorer mostra solo fatti utili | screenshot Monitor + grafo | ☐ |
| 2 | Sostituzione → UPDATES, query solo nuovo | Secondo documento che aggiorna lo stesso fatto; Dream; toggle «Solo correnti»; Query Panel sulla stessa domanda | screenshot nodo storico + risposta query | ☐ |
| 3 | Catena A←B←C, solo C latest | Terza sostituzione; doppio click su C evidenzia catena | screenshot highlight storia | ☐ |
| 4 | EXTENDS entrambi correnti | Due fatti complementari; Dream; entrambi visibili come correnti; Query li cita insieme | screenshot grafo + citazioni | ☐ |
| 5 | Consolidamento DERIVES | Gruppo di fatti simili → nodo astratto con DERIVES; sorgenti presenti | screenshot arco DERIVES | ☐ |
| 6 | Update su storico aggancia testa | Fatto vicino ad A storico → arco UPDATES verso B (testa), non A | screenshot / Neo4j Browser | ☐ |
| 7 | Riconciliazione drift=0 | Dopo Dream, Drift check = 0 nel Monitor / `POST /reconcile` | screenshot evento drift_check | ☐ |
| 8 | Query storica | Doppio click / history su C mostra C→B→A | screenshot dettaglio/history | ☐ |

Esecutore: _______________ Data: _______________
