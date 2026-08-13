/**
 * Visual encoding for entity / event / concept graph panels.
 *
 * Palette:
 * - entity → sky
 * - event → rose
 * - concept → violet
 *
 * Relationships:
 * - UPDATES → warning (full, wider)
 * - EXTENDS → info (full)
 * - PRECEDES / CAUSES / COOCCURS → dedicated colors (not EXTENDS fallback)
 * - PARTICIPATES → muted slate
 * - HAS_CONCEPT → stone
 *
 * F2.2: encodeNode/encodeRelationship reuse cached object identity when the
 * visual signature is unchanged, so InteractiveNvlWrapper does not treat the
 * whole graph as dirty on every unrelated re-render.
 */

import type { GraphNode, GraphRelationship } from "./types";

export const NODE_TYPE_COLORS = {
  entity: "#0369A1", // sky-700
  event: "#BE123C", // rose-700
  concept: "#6D28D9", // violet-700
} as const;

export const RELATION_COLORS = {
  UPDATES: "#D97706", // warning / amber-600
  EXTENDS: "#2563EB", // info / blue-600
  PRECEDES: "#0D9488", // teal-600 — temporal
  CAUSES: "#E11D48", // rose-600 — causal
  COOCCURS: "#7C3AED", // violet-600 — co-occurrence
  PARTICIPATES: "#94A3B8", // slate-400 — muted participation edges
  HAS_CONCEPT: "#A8A29E", // stone-400
} as const;

export const NODE_SIZE = 28;

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

function nodeTypeOf(node: GraphNode): keyof typeof NODE_TYPE_COLORS {
  const raw = String(node.properties?.type ?? "entity");
  if (raw in NODE_TYPE_COLORS) {
    return raw as keyof typeof NODE_TYPE_COLORS;
  }
  return "entity";
}

export function encodeNode(
  node: GraphNode,
  options: {
    selectedId?: string | null;
    dimmed?: boolean;
    highlighted?: boolean;
  } = {},
): NvlNode {
  const type = nodeTypeOf(node);
  const base = NODE_TYPE_COLORS[type];
  const selected = options.selectedId === node.id;
  const highlighted = options.highlighted === true;
  const dimmed = options.dimmed === true;

  const signature = [
    type,
    selected ? "1" : "0",
    dimmed ? "1" : "0",
    highlighted ? "1" : "0",
    node.caption || node.id,
  ].join("|");

  const cached = nodeCache.get(node.id);
  if (cached && cached.signature === signature) {
    return cached.value;
  }

  let color: string = base;
  if (dimmed && !selected && !highlighted) {
    color = hexToRgba(base, 0.25);
  }

  const captionBase = node.caption || node.id;
  const caption =
    captionBase.length > 48 ? `${captionBase.slice(0, 45)}…` : captionBase;

  const value: NvlNode = {
    id: node.id,
    caption,
    size: NODE_SIZE,
    color,
    selected: selected || highlighted,
  };
  nodeCache.set(node.id, { signature, value });
  return value;
}

export function encodeRelationship(
  rel: GraphRelationship,
  options: { dimmed?: boolean; highlighted?: boolean } = {},
): NvlRelationship {
  const typeKey = rel.type.toUpperCase() as keyof typeof RELATION_COLORS;
  const baseColor: string =
    RELATION_COLORS[typeKey] ?? RELATION_COLORS.EXTENDS;
  const width = typeKey === "UPDATES" ? 2.5 : 2;
  const dimmed = options.dimmed === true;
  const highlighted = options.highlighted === true;

  const signature = [
    typeKey,
    dimmed ? "1" : "0",
    highlighted ? "1" : "0",
    rel.from,
    rel.to,
    rel.caption ?? rel.type,
  ].join("|");

  const cached = relCache.get(rel.id);
  if (cached && cached.signature === signature) {
    return cached.value;
  }

  let finalColor = baseColor;
  if (dimmed && !highlighted) {
    finalColor = hexToRgba(baseColor, 0.2);
  }

  const value: NvlRelationship = {
    id: rel.id,
    from: rel.from,
    to: rel.to,
    caption: rel.caption ?? rel.type,
    color: finalColor,
    width: highlighted ? width + 1 : width,
    selected: highlighted,
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
    queryNodeIds?: Set<string> | null;
    queryRelKeys?: Set<string> | null;
  } = {},
): { nodes: NvlNode[]; rels: NvlRelationship[] } {
  const queryActive = Boolean(options.queryNodeIds?.size);

  const nvlNodes = nodes
    .filter((n) => !String(n.id).toLowerCase().startsWith("chunk"))
    .map((node) => {
      const inQuery = options.queryNodeIds?.has(node.id) ?? false;
      const dimmed = queryActive && !inQuery;
      return encodeNode(node, {
        selectedId: options.selectedId,
        dimmed,
        highlighted: queryActive && inQuery,
      });
    });

  const nvlRels = relationships.map((rel) => {
    const relKey = `${rel.from}->${rel.to}:${rel.type.toLowerCase()}`;
    const inQuery = options.queryRelKeys?.has(relKey) ?? false;
    const dimmed = queryActive && !inQuery;
    return encodeRelationship(rel, {
      dimmed,
      highlighted: inQuery,
    });
  });

  pruneCaches(
    new Set(nvlNodes.map((n) => n.id)),
    new Set(nvlRels.map((r) => r.id)),
  );

  return { nodes: nvlNodes, rels: nvlRels };
}
