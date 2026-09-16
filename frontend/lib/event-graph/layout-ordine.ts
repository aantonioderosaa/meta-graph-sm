/**
 * Pure preset positions and overview/drill-in filtering for Livello 1 "Ordine".
 * No cytoscape — unit-testable without a browser.
 *
 * Overview: Zona hubs on the trunk (click opens that zona).
 * Focused: the chosen Zona plus its eventi, stacked as siblings — not a
 * Cytoscape compound box.
 */

import type {
  EventGraphElements,
  EventGraphNodeData,
} from "./types";

export const H_GAP = 280;
export const V_GAP = 70;

export type OrdinePosition = { x: number; y: number };

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

export function isZonaNode(
  node: { data?: EventGraphNodeData } | EventGraphNodeData | null | undefined,
): boolean {
  const data = node ? nodeData(node) : null;
  return data != null && String(data.tipo ?? "") === "Zona";
}

/** Compact hub label — the riassunto stays on the tooltip / inspector. */
export function zonaDisplayLabel(
  node: { data?: EventGraphNodeData } | EventGraphNodeData | null | undefined,
): string {
  const data = node ? nodeData(node) : null;
  if (!data) return "";
  if (data.ordinale != null && String(data.ordinale) !== "") {
    const n = Number(data.ordinale);
    if (Number.isFinite(n)) return `Zona ${n}`;
  }
  return String(data.label ?? data.id ?? "");
}

/**
 * Overview = only Zona hubs + SUCCESSIONE_ZONA.
 * Focused = that Zona + eventi with ``parent`` = zona id, plus edges among them.
 */
export function filterOrdineElements(
  elements: EventGraphElements | null | undefined,
  focusedZonaId: string | null,
): EventGraphElements {
  const nodes = elements?.nodes ?? [];
  const edges = elements?.edges ?? [];
  const zonas = nodes.filter((node) => isZonaNode(node));
  const zonaIds = new Set(
    zonas
      .map((node) => nodeData(node)?.id)
      .filter((id): id is string => Boolean(id)),
  );
  const focusedExists =
    focusedZonaId != null && focusedZonaId !== "" && zonaIds.has(focusedZonaId);

  if (!focusedExists) {
    return {
      nodes: zonas,
      edges: edges.filter((edge) => {
        const tipo = String(edge.data?.tipo ?? "");
        return (
          tipo === "SUCCESSIONE_ZONA" &&
          zonaIds.has(edge.data.source) &&
          zonaIds.has(edge.data.target)
        );
      }),
    };
  }

  const keep = new Set<string>([focusedZonaId]);
  for (const node of nodes) {
    const data = nodeData(node);
    if (!data?.id) continue;
    if (data.parent === focusedZonaId) keep.add(data.id);
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
