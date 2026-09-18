/**
 * Closed vocabs for EventQuerySpec — keep in sync with
 * backend/app/models/event_graph.py (EventQuerySpec, TipoRelazione,
 * TempoVerbale, TraversalKind).
 */

import type {
  EventQuerySpec,
  Fattualita,
  FinestraTempoAssoluto,
  PianoNarrativo,
  TempoVerbale,
  TipoRelazione,
  TraversalKind,
} from "./types";

export const QUERY_PIANI: PianoNarrativo[] = [
  "PRIMO_PIANO",
  "SFONDO",
  "FUORI_LINEA",
];

export const QUERY_FATTUALITA: Fattualita[] = [
  "FATTUALE",
  "NON_FATTUALE",
  "IPOTETICO",
];

export const QUERY_TEMPI: TempoVerbale[] = [
  "presente",
  "imperfetto",
  "passato",
  "futuro",
  "non_finito",
  "trapassato",
];

export const QUERY_TRAVERSALS: TraversalKind[] = [
  "catena_di",
  "spina_dorsale_di",
  "prima_di",
  "dopo_di",
  "vicinato_temporale",
];

export const QUERY_RELAZIONI: TipoRelazione[] = [
  "SOGG",
  "OGG",
  "OBL",
  "TEMPO",
  "LUOGO",
  "MODO",
  "CAUSA",
  "LIMITE",
  "CONDIZIONE",
  "SCOPO",
  "CONCESSIONE",
  "CONTRASTO",
  "SEQUENZA",
  "CONTENUTO",
  "COLLEGATO",
  "SATELLITE_DI",
  "APPARTIENE_A",
  "SUCCESSIONE_ANCORA",
];

export const QUERY_TAB_ORDER = ["nl", "structured"] as const;
export type QueryTab = (typeof QUERY_TAB_ORDER)[number];

const SPEC_KEYS: (keyof EventQuerySpec)[] = [
  "lemma",
  "testo",
  "piano",
  "fattualita",
  "tempo",
  "fonte",
  "tipo_relazione",
  "finestra_tempo_assoluto",
  "documento",
  "traversal",
  "traversal_target",
];

export function traversalNeedsTarget(traversal?: TraversalKind | "" | null): boolean {
  return Boolean(traversal);
}

export function buildFinestraTempoAssoluto(
  da: string,
  a: string,
): FinestraTempoAssoluto | undefined {
  const next: FinestraTempoAssoluto = {};
  if (da.trim()) next.da = da.trim();
  if (a.trim()) next.a = a.trim();
  return next.da || next.a ? next : undefined;
}

export function buildEventQuerySpec(
  spec: EventQuerySpec,
  finestraDa = "",
  finestraA = "",
): EventQuerySpec {
  const next: EventQuerySpec = {};
  for (const key of SPEC_KEYS) {
    if (key === "finestra_tempo_assoluto") continue;
    const value = spec[key];
    if (value == null || value === "") continue;
    (next as Record<string, unknown>)[key] = value;
  }
  const finestra = buildFinestraTempoAssoluto(finestraDa, finestraA);
  if (finestra) next.finestra_tempo_assoluto = finestra;
  return next;
}

export function structuredQueryReady(spec: EventQuerySpec): boolean {
  if (!spec.traversal) return true;
  return Boolean(spec.traversal_target?.trim());
}
