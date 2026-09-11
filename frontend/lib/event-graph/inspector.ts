import type {
  ArcoDettaglio,
  EventGraphEdgeData,
  EventGraphElements,
  EventGraphNodeData,
  NodoDettaglio,
} from "./types";

const NODE_OMIT = new Set(["id", "label", "tipo"]);
const EDGE_OMIT = new Set(["id", "source", "target", "tipo", "label"]);

function proprietaFrom(
  data: Record<string, unknown>,
  omit: Set<string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(data)) {
    if (omit.has(key) || value == null || value === "") continue;
    out[key] = value;
  }
  return out;
}

function nodeById(
  elements: EventGraphElements | null | undefined,
  id: string | null | undefined,
): EventGraphNodeData | undefined {
  if (!id || !elements) return undefined;
  return elements.nodes.find((node) => node.data.id === id)?.data;
}

function endpointFromNode(
  node: EventGraphNodeData | undefined,
  fallbackId: string,
): ArcoDettaglio["source"] {
  const labels = node?.tipo ? [String(node.tipo)] : [];
  return {
    id: node?.id ?? fallbackId,
    labels,
    label: node?.label ?? node?.etichetta ?? node?.id ?? fallbackId,
  };
}

/** Dashboard payload from the visible graph when GET /arco/{id} 404s
 * (rels without ``r.id``: SOGG, SUCCESSIONE_ZONA, CONTEMPORANEO, livello 3). */
export function dettaglioArcoFromElements(
  id: string,
  elements?: EventGraphElements | null,
): ArcoDettaglio | null {
  const edge = elements?.edges.find((item) => item.data.id === id);
  if (!edge) return null;
  const data = edge.data as EventGraphEdgeData & Record<string, unknown>;
  return {
    id,
    tipo: data.tipo,
    proprieta: proprietaFrom(data as Record<string, unknown>, EDGE_OMIT),
    source: endpointFromNode(nodeById(elements, data.source), data.source),
    target: endpointFromNode(nodeById(elements, data.target), data.target),
  };
}

export function dettaglioNodoFromElements(
  id: string,
  elements?: EventGraphElements | null,
): NodoDettaglio | null {
  const node = nodeById(elements, id);
  if (!node) return null;
  const labels = node.tipo ? [String(node.tipo)] : [];
  return {
    id,
    labels,
    proprieta: proprietaFrom(node as Record<string, unknown>, NODE_OMIT),
  };
}
