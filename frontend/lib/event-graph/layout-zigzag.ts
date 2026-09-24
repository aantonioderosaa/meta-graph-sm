/**
 * Two-column zigzag (snake) of Evento nodes in exposition order.
 * Isolated to the Relazioni view — Tutto and Ordine keep their own layouts.
 */

import { ARGOMENTALI } from "./encoding";
import type {
  EventGraphEdgeElement,
  EventGraphElements,
  EventGraphNodeData,
} from "./types";

export const ZIGZAG_H_GAP = 320;
export const ZIGZAG_V_GAP = 72;
export const ZIGZAG_NODE_SIZE = 16;
export const ZIGZAG_EVENT_COLOR = "#1E293B";
export const SATELLITE_OFFSET = 36;

export type ZigzagPosition = { x: number; y: number };

const HUB_TIPI = new Set(["Zona", "AncoraTemporale", "ClusterTemporale"]);
const HIDDEN_EDGE_LABELS = new Set(["COLLEGATO", "SATELLITE_DI"]);

function asFinite(value: unknown): number | null {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function nodeData(
  node: { data?: EventGraphNodeData } | EventGraphNodeData | null | undefined,
): EventGraphNodeData | null {
  if (!node || typeof node !== "object") return null;
  if ("data" in node && node.data && typeof node.data === "object" && "id" in node.data) {
    return node.data;
  }
  if ("id" in node) return node as EventGraphNodeData;
  return null;
}

export function isEventoNode(
  node: { data?: EventGraphNodeData } | EventGraphNodeData | null | undefined,
): boolean {
  const data = node ? nodeData(node) : null;
  if (!data) return false;
  const tipo = String(data.tipo ?? "Fatto");
  return tipo === "Fatto" || tipo === "";
}

export function chiaveEsposizione(
  data: EventGraphNodeData,
): [number, number, number, string] {
  return [
    asFinite(data.posizione_doc) ?? Number.POSITIVE_INFINITY,
    asFinite(data.posizione_chunk) ?? Number.POSITIVE_INFINITY,
    asFinite(data.offset_inizio) ?? Number.POSITIVE_INFINITY,
    String(data.id ?? ""),
  ];
}

export function hasEsposizione(data: EventGraphNodeData): boolean {
  return (
    asFinite(data.posizione_doc) != null ||
    asFinite(data.posizione_chunk) != null ||
    asFinite(data.offset_inizio) != null
  );
}

function compareChiave(a: EventGraphNodeData, b: EventGraphNodeData): number {
  const ka = chiaveEsposizione(a);
  const kb = chiaveEsposizione(b);
  for (let i = 0; i < 3; i += 1) {
    if (ka[i] !== kb[i]) return ka[i] < kb[i] ? -1 : 1;
  }
  return ka[3] < kb[3] ? -1 : ka[3] > kb[3] ? 1 : 0;
}

function topologicalSequenza(
  ids: string[],
  edges: EventGraphEdgeElement[],
): string[] {
  const idSet = new Set(ids);
  const indeg = new Map(ids.map((id) => [id, 0]));
  const adj = new Map(ids.map((id) => [id, [] as string[]]));
  for (const edge of edges) {
    if (String(edge.data?.tipo ?? "") !== "SEQUENZA") continue;
    const source = edge.data.source;
    const target = edge.data.target;
    if (!idSet.has(source) || !idSet.has(target) || source === target) continue;
    adj.get(source)?.push(target);
    indeg.set(target, (indeg.get(target) ?? 0) + 1);
  }
  for (const neighbours of adj.values()) neighbours.sort();
  const queue = ids.filter((id) => indeg.get(id) === 0).sort();
  const out: string[] = [];
  while (queue.length > 0) {
    const current = queue.shift();
    if (!current) break;
    out.push(current);
    for (const next of adj.get(current) ?? []) {
      const remaining = (indeg.get(next) ?? 1) - 1;
      indeg.set(next, remaining);
      if (remaining === 0) {
        queue.push(next);
        queue.sort();
      }
    }
  }
  const leftover = ids.filter((id) => !out.includes(id)).sort();
  return [...out, ...leftover];
}

export function orderedEventoIds(elements: EventGraphElements): string[] {
  const events = (elements?.nodes ?? [])
    .map((node) => nodeData(node))
    .filter((data): data is EventGraphNodeData => Boolean(data?.id && isEventoNode(data)));
  if (events.length === 0) return [];
  if (events.some(hasEsposizione)) {
    return [...events].sort(compareChiave).map((data) => data.id);
  }
  return topologicalSequenza(
    events.map((data) => data.id),
    elements?.edges ?? [],
  );
}

function findAnchorId(
  nodeId: string,
  eventIds: Set<string>,
  edges: EventGraphEdgeElement[],
): string | null {
  const satellite = edges.find(
    (edge) =>
      String(edge.data?.tipo ?? "") === "SATELLITE_DI" &&
      edge.data.source === nodeId &&
      eventIds.has(edge.data.target),
  );
  if (satellite) return satellite.data.target;
  const linked = edges.find(
    (edge) =>
      (edge.data.source === nodeId && eventIds.has(edge.data.target)) ||
      (edge.data.target === nodeId && eventIds.has(edge.data.source)),
  );
  if (!linked) return null;
  return eventIds.has(linked.data.source)
    ? linked.data.source
    : linked.data.target;
}

/** Tipo drawn on zigzag edges; skip argument/placeholder noise. */
export function ladderEdgeLabel(tipo: string | null | undefined): string {
  const normalized = String(tipo ?? "").toUpperCase();
  if (
    !normalized ||
    (ARGOMENTALI as Set<string>).has(normalized) ||
    HIDDEN_EDGE_LABELS.has(normalized)
  ) {
    return "";
  }
  return normalized;
}

export function positionsZigzag(
  elements: EventGraphElements,
  origin: ZigzagPosition = { x: 0, y: 0 },
): Record<string, ZigzagPosition> {
  const positions: Record<string, ZigzagPosition> = {};
  const ordered = orderedEventoIds(elements);
  const eventIds = new Set(ordered);
  ordered.forEach((id, index) => {
    positions[id] = {
      x: origin.x + (index % 2 === 0 ? 0 : ZIGZAG_H_GAP),
      y: origin.y + index * ZIGZAG_V_GAP,
    };
  });

  const satellitesByAnchor = new Map<string, string[]>();
  const leftovers: string[] = [];
  for (const node of elements?.nodes ?? []) {
    const data = nodeData(node);
    if (!data?.id || data.id in positions) continue;
    if (HUB_TIPI.has(String(data.tipo ?? ""))) continue;
    const anchor = findAnchorId(data.id, eventIds, elements?.edges ?? []);
    if (anchor && anchor in positions) {
      const list = satellitesByAnchor.get(anchor) ?? [];
      list.push(data.id);
      satellitesByAnchor.set(anchor, list);
    } else {
      leftovers.push(data.id);
    }
  }

  for (const [anchor, satelliteIds] of satellitesByAnchor) {
    const anchorPos = positions[anchor];
    if (!anchorPos) continue;
    const outward = anchorPos.x === origin.x ? -1 : 1;
    satelliteIds.forEach((id, index) => {
      const mid = (satelliteIds.length - 1) / 2;
      positions[id] = {
        x: anchorPos.x + outward * SATELLITE_OFFSET,
        y: anchorPos.y + (index - mid) * 18,
      };
    });
  }

  leftovers.forEach((id, index) => {
    positions[id] = {
      x: origin.x - SATELLITE_OFFSET * 2,
      y: origin.y + index * 40,
    };
  });

  return positions;
}
