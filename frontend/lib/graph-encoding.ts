/**
 * Visual encoding for Graph Explorer (tech-spec §11.1, E7.2).
 *
 * Palette (project colors, not purple-default):
 * - fact → teal primary
 * - preference → amber secondary
 * - episode → slate tertiary
 *
 * Historical (is_latest=false): reduced opacity via rgba + caption marker.
 * NVL has no native dashed node border; opacity + "(storico)" caption is the
 * accessible stand-in documented in the visual checklist.
 *
 * Relationships:
 * - UPDATES → warning (full, wider)
 * - EXTENDS → info (full)
 * - DERIVES → success (thinner; NVL has no dashed stroke — thinner width is the proxy)
 *
 * F2.2: encodeNode/encodeRelationship reuse cached object identity when the
 * visual signature is unchanged, so InteractiveNvlWrapper does not treat the
 * whole graph as dirty on every unrelated re-render.
 */

import type { GraphNode, GraphRelationship } from "./types";

export const FACT_TYPE_COLORS = {
  fact: "#0F766E", // teal-700
  preference: "#B45309", // amber-700
  episode: "#475569", // slate-600
} as const;

export const RELATION_COLORS = {
  UPDATES: "#D97706", // warning / amber-600
  EXTENDS: "#2563EB", // info / blue-600
  DERIVES: "#16A34A", // success / green-600
} as const;

export const NODE_SIZE = 28;
export const HISTORICAL_ALPHA = 0.4;

export type NvlNode = {
  id: string;
  caption: string;
  size: number;
  color: string;
  selected?: boolean;
  disabled?: boolean;
};

export type NvlRelationship = {
  id: string;
  from: string;
  to: string;
  caption: string;
  color: string;
  width: number;
  selected?: boolean;
};

type CacheEntry<T> = { signature: string; value: T };

const nodeCache = new Map<string, CacheEntry<NvlNode>>();
const relCache = new Map<string, CacheEntry<NvlRelationship>>();

function hexToRgba(hex: string, alpha: number): string {
  const normalized = hex.replace("#", "");
  const r = Number.parseInt(normalized.slice(0, 2), 16);
  const g = Number.parseInt(normalized.slice(2, 4), 16);
  const b = Number.parseInt(normalized.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function factTypeOf(node: GraphNode): keyof typeof FACT_TYPE_COLORS {
  const raw = String(node.properties?.type ?? "fact");
  if (raw === "preference" || raw === "episode" || raw === "fact") {
    return raw;
  }
  return "fact";
}

export function isHistoricalNode(node: GraphNode): boolean {
  return node.properties?.is_latest === false;
}

export function encodeNode(
  node: GraphNode,
  options: {
    selectedId?: string | null;
    dimmed?: boolean;
    historyHighlighted?: boolean;
    pulsing?: boolean;
  } = {},
): NvlNode {
  const type = factTypeOf(node);
  const historical = isHistoricalNode(node);
  const base = FACT_TYPE_COLORS[type];
  const selected = options.selectedId === node.id;
  const historyHighlighted = options.historyHighlighted === true;
  const pulsing = options.pulsing === true;
  const dimmed = options.dimmed === true;

  const signature = [
    type,
    historical ? "1" : "0",
    selected ? "1" : "0",
    dimmed ? "1" : "0",
    historyHighlighted ? "1" : "0",
    pulsing ? "1" : "0",
    node.caption || node.id,
  ].join("|");

  const cached = nodeCache.get(node.id);
  if (cached && cached.signature === signature) {
    return cached.value;
  }

  let color = historical ? hexToRgba(base, HISTORICAL_ALPHA) : base;
  if (dimmed && !selected && !historyHighlighted && !pulsing) {
    color = hexToRgba(base, historical ? 0.15 : 0.25);
  }
  if (historyHighlighted || pulsing) {
    color = base;
  }

  const captionBase = node.caption || node.id;
  const caption = historical ? `${captionBase} (storico)` : captionBase;

  const value: NvlNode = {
    id: node.id,
    caption: caption.length > 48 ? `${caption.slice(0, 45)}…` : caption,
    size: pulsing ? NODE_SIZE + 10 : NODE_SIZE,
    color,
    selected: selected || historyHighlighted || pulsing,
    disabled: historical && !historyHighlighted && !selected && !pulsing,
  };
  nodeCache.set(node.id, { signature, value });
  return value;
}

export function encodeRelationship(
  rel: GraphRelationship,
  options: { dimmed?: boolean; historyHighlighted?: boolean } = {},
): NvlRelationship {
  const typeKey = rel.type.toUpperCase() as keyof typeof RELATION_COLORS;
  const baseColor: string =
    RELATION_COLORS[typeKey] ?? RELATION_COLORS.EXTENDS;
  const width = typeKey === "DERIVES" ? 1 : typeKey === "UPDATES" ? 2.5 : 2;
  const dimmed = options.dimmed === true;
  const historyHighlighted = options.historyHighlighted === true;

  const signature = [
    typeKey,
    dimmed ? "1" : "0",
    historyHighlighted ? "1" : "0",
    rel.from,
    rel.to,
    rel.caption ?? rel.type,
  ].join("|");

  const cached = relCache.get(rel.id);
  if (cached && cached.signature === signature) {
    return cached.value;
  }

  let finalColor = baseColor;
  if (dimmed && !historyHighlighted) {
    finalColor = hexToRgba(baseColor, 0.2);
  }

  const value: NvlRelationship = {
    id: rel.id,
    from: rel.from,
    to: rel.to,
    caption: rel.caption ?? rel.type,
    color: finalColor,
    width: historyHighlighted ? width + 1 : width,
    selected: historyHighlighted,
  };
  relCache.set(rel.id, { signature, value });
  return value;
}

function pruneCaches(activeNodeIds: Set<string>, activeRelIds: Set<string>): void {
  for (const id of nodeCache.keys()) {
    if (!activeNodeIds.has(id)) {
      nodeCache.delete(id);
    }
  }
  for (const id of relCache.keys()) {
    if (!activeRelIds.has(id)) {
      relCache.delete(id);
    }
  }
}

/** Test helper: current encoding cache sizes (nodes, rels). */
export function getEncodingCacheSize(): { nodes: number; rels: number } {
  return { nodes: nodeCache.size, rels: relCache.size };
}

/** Test helper: clear encoding caches between cases. */
export function clearEncodingCache(): void {
  nodeCache.clear();
  relCache.clear();
}

export function toNvlGraph(
  nodes: GraphNode[],
  relationships: GraphRelationship[],
  options: {
    selectedId?: string | null;
    historyNodeIds?: Set<string> | null;
    historyRelIds?: Set<string> | null;
    queryNodeIds?: Set<string> | null;
    queryRelKeys?: Set<string> | null;
    pulsingIds?: Set<string> | null;
  } = {},
): { nodes: NvlNode[]; rels: NvlRelationship[] } {
  const historyActive = Boolean(options.historyNodeIds?.size);
  const queryActive = Boolean(options.queryNodeIds?.size);

  const nvlNodes = nodes
    .filter((n) => !String(n.id).toLowerCase().startsWith("chunk"))
    .map((node) => {
      const inHistory = options.historyNodeIds?.has(node.id) ?? false;
      const inQuery = options.queryNodeIds?.has(node.id) ?? false;
      const pulsing = options.pulsingIds?.has(node.id) ?? false;
      const dimmed =
        (historyActive && !inHistory) ||
        (queryActive && !inQuery && !historyActive);
      return encodeNode(node, {
        selectedId: options.selectedId,
        dimmed,
        historyHighlighted: inHistory || (queryActive && inQuery),
        pulsing,
      });
    });

  const nvlRels = relationships.map((rel) => {
    const inHistory = options.historyRelIds?.has(rel.id) ?? false;
    const relKey = `${rel.from}->${rel.to}:${rel.type.toLowerCase()}`;
    const inQuery = options.queryRelKeys?.has(relKey) ?? false;
    const pulsing =
      (options.pulsingIds?.has(rel.id) ||
        options.pulsingIds?.has(rel.from) ||
        options.pulsingIds?.has(rel.to)) ??
      false;
    const dimmed =
      (historyActive && !inHistory) ||
      (queryActive && !inQuery && !historyActive);
    return encodeRelationship(rel, {
      dimmed,
      historyHighlighted: inHistory || inQuery || pulsing,
    });
  });

  pruneCaches(
    new Set(nvlNodes.map((n) => n.id)),
    new Set(nvlRels.map((r) => r.id)),
  );

  return { nodes: nvlNodes, rels: nvlRels };
}
