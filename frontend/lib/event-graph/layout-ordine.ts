/**
 * Pure preset positions for the Livello 1 "Ordine" trunk layout.
 * No cytoscape — unit-testable without a browser.
 */

import type { EventGraphElements, EventGraphNodeData } from "./types";

export const H_GAP = 280;
export const V_GAP = 70;

export type OrdinePosition = { x: number; y: number };

function asOrdinale(value: unknown): number {
  if (value == null || value === "") return 0;
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function nodeData(node: { data?: EventGraphNodeData } | EventGraphNodeData): EventGraphNodeData | null {
  if (!node || typeof node !== "object") return null;
  if ("data" in node && node.data && typeof node.data === "object" && "id" in node.data) {
    return node.data;
  }
  if ("id" in node) return node as EventGraphNodeData;
  return null;
}

export function positionsOrdine(
  elements: EventGraphElements,
): Record<string, OrdinePosition> {
  const positions: Record<string, OrdinePosition> = {};
  const nodes = elements?.nodes ?? [];
  const zonaOrdinale = new Map<string, number>();

  for (const node of nodes) {
    const data = nodeData(node);
    if (!data?.id) continue;
    if (String(data.tipo ?? "") !== "Zona") continue;
    const ordinale = asOrdinale(data.ordinale);
    zonaOrdinale.set(data.id, ordinale);
    positions[data.id] = { x: ordinale * H_GAP, y: 0 };
  }

  const childrenByParent = new Map<string, string[]>();
  for (const node of nodes) {
    const data = nodeData(node);
    if (!data?.id) continue;
    const parentId = data.parent;
    if (parentId == null || parentId === "") continue;
    if (!zonaOrdinale.has(parentId) && !(parentId in positions)) continue;
    const list = childrenByParent.get(parentId) ?? [];
    list.push(data.id);
    childrenByParent.set(parentId, list);
  }

  for (const [parentId, childIds] of childrenByParent) {
    const parentPos = positions[parentId];
    if (!parentPos) continue;
    const ordinale = zonaOrdinale.get(parentId) ?? 0;
    const sign = ordinale % 2 === 0 ? -1 : 1;
    childIds.forEach((id, index) => {
      positions[id] = { x: parentPos.x, y: sign * (index + 1) * V_GAP };
    });
  }

  return positions;
}
