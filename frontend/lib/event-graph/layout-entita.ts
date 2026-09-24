/**
 * Pure preset positions and overview/drill-in filtering for Livello 1 "Entità".
 * No cytoscape — unit-testable without a browser.
 *
 * Overview: KernelCategoria hubs (9 total) + SUCCESSIONE_ZONA.
 * Focused: that KernelCategoria + its menzioni, stacked as siblings — not a
 * Cytoscape compound box.
 */

import type {
  EventGraphElements,
  EventGraphNodeData,
} from "./types";

export const H_GAP = 280;
export const V_GAP = 70;

export type EntitaPosition = { x: number; y: number };

function asOrdinale(value: unknown): number {
  if (value == null || value === "") return 0;
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function nodeData(
  node: { data?: EventGraphNodeData } | EventGraphNodeData,
): EventGraphNodeData | null {
  if (!node || typeof node !== "object") return null;
  if ("data" in node && node.data && typeof node.data === "object" && "id" in node.data) {
    return node.data;
  }
  if ("id" in node) return node as EventGraphNodeData;
  return null;
}

export function isKernelCategoriaNode(
  node: { data?: EventGraphNodeData } | EventGraphNodeData | null | undefined,
): boolean {
  const data = node ? nodeData(node) : null;
  return data != null && String(data.tipo ?? "") === "KernelCategoria";
}

/**
 * Overview = only KernelCategoria hubs + SUCCESSIONE_ZONA.
 * Focused = that KernelCategoria + menzioni with ``parent`` = categoria id, plus edges among them.
 */
export function filterEntitaElements(
  elements: EventGraphElements | null | undefined,
  focusedCategoriaId: string | null,
): EventGraphElements {
  const nodes = elements?.nodes ?? [];
  const edges = elements?.edges ?? [];
  const categorias = nodes.filter((node) => isKernelCategoriaNode(node));
  const categoriaIds = new Set(
    categorias
      .map((node) => nodeData(node)?.id)
      .filter((id): id is string => Boolean(id)),
  );
  const focusedExists =
    focusedCategoriaId != null && focusedCategoriaId !== "" && categoriaIds.has(focusedCategoriaId);

  if (!focusedExists) {
    return {
      nodes: categorias,
      edges: edges.filter((edge) => {
        const tipo = String(edge.data?.tipo ?? "");
        return (
          tipo === "SUCCESSIONE_ZONA" &&
          categoriaIds.has(edge.data.source) &&
          categoriaIds.has(edge.data.target)
        );
      }),
    };
  }

  const keep = new Set<string>([focusedCategoriaId]);
  for (const node of nodes) {
    const data = nodeData(node);
    if (!data?.id) continue;
    if (data.parent === focusedCategoriaId) keep.add(data.id);
  }
  return {
    nodes: nodes.filter((node) => {
      const data = nodeData(node);
      return data != null && keep.has(data.id);
    }),
    edges: edges.filter(
      (edge) => keep.has(edge.data.source) && keep.has(edge.data.target),
    ),
  };
}

export function positionsEntita(
  elements: EventGraphElements,
): Record<string, EntitaPosition> {
  const positions: Record<string, EntitaPosition> = {};
  const nodes = elements?.nodes ?? [];
  const categoriaOrdinale = new Map<string, number>();

  for (const node of nodes) {
    const data = nodeData(node);
    if (!data?.id) continue;
    if (String(data.tipo ?? "") !== "KernelCategoria") continue;
    const ordinale = asOrdinale(data.ordinale);
    categoriaOrdinale.set(data.id, ordinale);
    positions[data.id] = { x: ordinale * H_GAP, y: 0 };
  }

  const childrenByParent = new Map<string, string[]>();
  for (const node of nodes) {
    const data = nodeData(node);
    if (!data?.id) continue;
    const parentId = data.parent;
    if (parentId == null || parentId === "") continue;
    if (!categoriaOrdinale.has(parentId) && !(parentId in positions)) continue;
    const list = childrenByParent.get(parentId) ?? [];
    list.push(data.id);
    childrenByParent.set(parentId, list);
  }

  for (const [parentId, childIds] of childrenByParent) {
    const parentPos = positions[parentId];
    if (!parentPos) continue;
    const ordinale = categoriaOrdinale.get(parentId) ?? 0;
    const sign = ordinale % 2 === 0 ? -1 : 1;
    childIds.forEach((id, index) => {
      positions[id] = { x: parentPos.x, y: sign * (index + 1) * V_GAP };
    });
  }

  return positions;
}