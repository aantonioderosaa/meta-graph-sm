/**
 * Pure legend helpers (piano 15.5 / M22). No cytoscape.
 */

import {
  CATENA_COLOR,
  MENZIONE_COLOR,
  PIANO_COLORS,
  PIANO_FALLBACK,
  QUARANTENA_COLOR,
  encodeEdge,
  encodeNode,
} from "./encoding";
import type {
  CatalogArc,
  CatalogArcFamiglia,
  CatalogCatenaTrait,
  CatalogTraitValue,
  EventGraphCatalog,
  EventGraphEdgeData,
  EventGraphEdgeElement,
  EventGraphNodeData,
  EventGraphNodeElement,
  EventGraphStats,
} from "./types";

export type LegendFilter = {
  kind: "arco" | "nodo" | "tratto";
  key: string;
  value: string;
} | null;

export const ARC_FAMIGLIE: CatalogArcFamiglia[] = [
  "argomentali",
  "dizionario",
  "temporale",
  "placeholder",
  "struttura",
];

export const EMPTY_STATS: EventGraphStats = {
  nodi: { Fatto: 0, Menzione: 0, Quarantena: 0 },
  archi: {},
  tratti: { piano: {} },
};

export const LEGEND_DIM_OPACITY = 0.18;

export type LegendElement =
  | EventGraphNodeElement
  | EventGraphEdgeElement
  | EventGraphNodeData
  | EventGraphEdgeData
  | { data?: Record<string, unknown> }
  | Record<string, unknown>;

export type LegendArcGroup = {
  famiglia: CatalogArcFamiglia;
  arches: CatalogArc[];
};

function asRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object") return value as Record<string, unknown>;
  return {};
}

export function elementData(element: LegendElement): Record<string, unknown> {
  const raw = asRecord(element);
  if ("data" in raw && raw.data && typeof raw.data === "object") {
    return asRecord(raw.data);
  }
  return raw;
}

export function isGraphEdge(element: LegendElement): boolean {
  const data = elementData(element);
  return data.source != null && data.source !== "" && data.target != null && data.target !== "";
}

export function groupedArches(catalog?: EventGraphCatalog | null): LegendArcGroup[] {
  if (!catalog?.arches) return [];
  return ARC_FAMIGLIE.map((famiglia) => ({
    famiglia,
    arches: catalog.arches[famiglia] ?? [],
  })).filter((group) => group.arches.length > 0);
}

export function isCatenaTrait(
  value: CatalogTraitValue[] | CatalogCatenaTrait | undefined,
): value is CatalogCatenaTrait {
  return Boolean(
    value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      Array.isArray((value as CatalogCatenaTrait).ruoli),
  );
}

/** Flatten catalog trait values (arrays or the catena node-trait object). */
export function traitEntries(
  raw: CatalogTraitValue[] | CatalogCatenaTrait | undefined,
): Array<{ value: string; meaning?: string }> {
  if (raw == null) return [];
  if (isCatenaTrait(raw)) {
    return raw.ruoli.map((ruolo) => ({
      value: String(ruolo),
      meaning: raw.significato?.[String(ruolo)],
    }));
  }
  return raw.map((item) => ({ value: String(item) }));
}

export function swatchForArc(tipo: string): string {
  return encodeEdge({
    id: tipo,
    source: "s",
    target: "t",
    tipo,
  }).color;
}

export function swatchForNode(id: string): string {
  if (id === "Menzione") return encodeNode({ id, label: id, tipo: id }).color || MENZIONE_COLOR;
  if (id === "Quarantena") {
    return encodeNode({ id, label: id, tipo: id }).color || QUARANTENA_COLOR;
  }
  return encodeNode({
    id,
    label: id,
    tipo: id || "Fatto",
    piano: "PRIMO_PIANO",
    fattualita: "FATTUALE",
  }).color;
}

export function swatchForTrait(key: string, value: string): string {
  if (key === "piano" && value in PIANO_COLORS) {
    return PIANO_COLORS[value as keyof typeof PIANO_COLORS];
  }
  if (key === "fattualita") {
    return encodeNode({
      id: "trait",
      label: "trait",
      tipo: "Fatto",
      piano: "PRIMO_PIANO",
      fattualita: value,
    }).color;
  }
  if (key === "catena") return CATENA_COLOR;
  return PIANO_FALLBACK;
}

export function countFor(
  stats: EventGraphStats | null | undefined,
  filter: LegendFilter,
): number {
  if (!filter || !stats) return 0;
  if (filter.kind === "arco") {
    return Number(stats.archi?.[filter.value] ?? 0);
  }
  if (filter.kind === "nodo") {
    return Number(stats.nodi?.[filter.value] ?? 0);
  }
  const bucket = stats.tratti?.[filter.key];
  if (!bucket) return 0;
  return Number(bucket[filter.value] ?? 0);
}

export function elementMatches(element: LegendElement, filter: LegendFilter): boolean {
  if (!filter) return true;
  const data = elementData(element);
  const edge = isGraphEdge(data);

  if (filter.kind === "arco") {
    return edge && String(data.tipo ?? "") === filter.value;
  }
  if (filter.kind === "nodo") {
    return !edge && String(data.tipo ?? "") === filter.value;
  }
  if (edge) return false;
  const raw = data[filter.key];
  if (raw === undefined || raw === null) return false;
  return String(raw) === String(filter.value);
}

export function legendOpacity(
  baseOpacity: number,
  element: LegendElement,
  filter: LegendFilter,
): number {
  if (!filter || elementMatches(element, filter)) return baseOpacity;
  return LEGEND_DIM_OPACITY;
}

export function filtersEqual(a: LegendFilter, b: LegendFilter): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return a.kind === b.kind && a.key === b.key && a.value === b.value;
}

export function toggleLegendFilter(
  current: LegendFilter,
  next: NonNullable<LegendFilter>,
): LegendFilter {
  return filtersEqual(current, next) ? null : next;
}
