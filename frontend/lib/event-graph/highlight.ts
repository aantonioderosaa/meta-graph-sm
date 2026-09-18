/**
 * Merge two query-result id sets into overlay kinds (piano 15.4 / M21).
 * Pure — no cytoscape, no fetch.
 */

import type { EventNlQueryResponse, EventQueryRisultato } from "./types";

export type HighlightKind = "structured" | "nl" | "both";

export const HIGHLIGHT_COLORS: Record<HighlightKind, string> = {
  structured: "#22D3EE",
  nl: "#A855F7",
  both: "#F43F5E",
};

export function idsFromQueryResult(
  risultato?: EventQueryRisultato | null,
): string[] {
  const ids: string[] = [];
  for (const evento of risultato?.eventi ?? []) {
    if (evento?.id) ids.push(String(evento.id));
  }
  for (const arco of risultato?.archi ?? []) {
    if (!arco) continue;
    if (arco.source) ids.push(String(arco.source));
    if (arco.target) ids.push(String(arco.target));
  }
  return ids;
}

export function idsFromNlQuery(
  result?: EventNlQueryResponse | null,
): string[] {
  const ids = idsFromQueryResult(result?.risultato);
  for (const id of result?.eventi_citati ?? []) {
    if (id) ids.push(String(id));
  }
  return ids;
}

export function mergeHighlights(
  idsA: string[],
  idsB: string[],
): Record<string, HighlightKind> {
  const out: Record<string, HighlightKind> = {};
  for (const id of idsA) {
    if (!id) continue;
    out[id] = "structured";
  }
  for (const id of idsB) {
    if (!id) continue;
    out[id] = out[id] === "structured" ? "both" : "nl";
  }
  return out;
}
