/**
 * Relazioni view: force-directed (cose) placement of Evento nodes.
 * Spacing constants keep labels readable; layout stays organic, not a circle.
 */

import { isEventoNode, orderedEventoIds } from "./layout-zigzag";
import type { EventGraphElements, EventGraphNodeData } from "./types";

export const RELAZIONI_MIN_GAP = 260;
export const RELAZIONI_ISOLATE_GAP = 260;
export const RELAZIONI_NODE_WIDTH = 118;
export const RELAZIONI_NODE_HEIGHT = 52;
export const RELAZIONI_LABEL_MAX = 36;

export type RelazioniPosition = { x: number; y: number };

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

export function relazioniNodeLabel(
  raw: string | null | undefined,
  max = RELAZIONI_LABEL_MAX,
): string {
  const text = String(raw ?? "").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(1, max - 1))}…`;
}

function circleRadius(count: number): number {
  if (count <= 1) return 0;
  return Math.max(RELAZIONI_MIN_GAP, (count * RELAZIONI_MIN_GAP) / (2 * Math.PI));
}

function resolveOverlaps(
  positions: Record<string, RelazioniPosition>,
  minGap: number,
): void {
  const ids = Object.keys(positions);
  for (let pass = 0; pass < 8; pass += 1) {
    let moved = false;
    for (let i = 0; i < ids.length; i += 1) {
      for (let j = i + 1; j < ids.length; j += 1) {
        const a = positions[ids[i]];
        const b = positions[ids[j]];
        if (!a || !b) continue;
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let dist = Math.hypot(dx, dy);
        if (dist < 1e-6) {
          dx = 1;
          dy = 0;
          dist = 1;
        }
        if (dist >= minGap) continue;
        const push = (minGap - dist) / 2;
        const ux = dx / dist;
        const uy = dy / dist;
        a.x -= ux * push;
        a.y -= uy * push;
        b.x += ux * push;
        b.y += uy * push;
        moved = true;
      }
    }
    if (!moved) break;
  }
}

function placeGrid(
  ids: string[],
  origin: RelazioniPosition,
  positions: Record<string, RelazioniPosition>,
): void {
  const cols = Math.max(1, Math.ceil(Math.sqrt(ids.length)));
  ids.forEach((id, index) => {
    const col = index % cols;
    const row = Math.floor(index / cols);
    positions[id] = {
      x: origin.x + col * RELAZIONI_ISOLATE_GAP,
      y: origin.y + row * RELAZIONI_MIN_GAP,
    };
  });
}

export function positionsRelazioni(
  elements: EventGraphElements,
  origin: RelazioniPosition = { x: 0, y: 0 },
): Record<string, RelazioniPosition> {
  const positions: Record<string, RelazioniPosition> = {};
  const ordered = orderedEventoIds(elements);
  const eventIds = new Set(ordered);
  const linked = new Set<string>();
  for (const edge of elements?.edges ?? []) {
    const source = String(edge.data?.source ?? "");
    const target = String(edge.data?.target ?? "");
    if (!eventIds.has(source) || !eventIds.has(target) || source === target) {
      continue;
    }
    linked.add(source);
    linked.add(target);
  }
  const connected = ordered.filter((id) => linked.has(id));
  const isolated = ordered.filter((id) => !linked.has(id));

  if (connected.length <= 1) {
    if (connected[0]) {
      positions[connected[0]] = { x: origin.x, y: origin.y };
    }
    placeGrid(isolated, origin, positions);
    if (connected[0] && isolated.length > 0) {
      const rowY = origin.y + RELAZIONI_MIN_GAP * 1.5;
      isolated.forEach((id, index) => {
        positions[id] = {
          x: origin.x + index * RELAZIONI_ISOLATE_GAP,
          y: rowY,
        };
      });
    }
  } else {
    const n = connected.length;
    const radius = circleRadius(n);
    connected.forEach((id, index) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * index) / n;
      positions[id] = {
        x: origin.x + radius * Math.cos(angle),
        y: origin.y + radius * Math.sin(angle),
      };
    });
    const maxY = Math.max(...connected.map((id) => positions[id]?.y ?? origin.y));
    const rowY = maxY + RELAZIONI_MIN_GAP * 1.5;
    const totalW = Math.max(0, isolated.length - 1) * RELAZIONI_ISOLATE_GAP;
    isolated.forEach((id, index) => {
      positions[id] = {
        x: origin.x + index * RELAZIONI_ISOLATE_GAP - totalW / 2,
        y: rowY,
      };
    });
  }

  for (const node of elements?.nodes ?? []) {
    const data = nodeData(node);
    if (!data?.id || data.id in positions) continue;
    if (!isEventoNode(data)) continue;
    positions[data.id] = {
      x: origin.x - RELAZIONI_ISOLATE_GAP,
      y: origin.y,
    };
  }

  resolveOverlaps(positions, RELAZIONI_MIN_GAP);
  return positions;
}

/** Spacious cose options: same organic layout as before, readable node gap. */
export function coseRelazioniLayoutOptions(randomize = false): Record<string, unknown> {
  return {
    name: "cose",
    animate: false,
    fit: false,
    padding: 48,
    randomize,
    nodeDimensionsIncludeLabels: true,
    nodeRepulsion: () => 18000,
    nodeOverlap: 48,
    idealEdgeLength: () => RELAZIONI_MIN_GAP,
    edgeElasticity: () => 120,
    nestingFactor: 1.2,
    gravity: 0.2,
    numIter: 2000,
    initialTemp: 300,
    coolingFactor: 0.95,
    minTemp: 1.0,
    componentSpacing: RELAZIONI_ISOLATE_GAP,
  };
}
