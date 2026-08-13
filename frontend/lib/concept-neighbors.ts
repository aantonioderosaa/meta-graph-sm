/**
 * Helpers for the concept-bridge highlight (Macrotask 7): click a Concept,
 * collect neighboring entity/event ids, highlight them in the other panels.
 */

import type { GraphResponse } from "./types";

/** Neighbor node ids from GET /graph/concepts/{id}, excluding the concept itself. */
export function idsFromConceptNeighbors(
  graph: GraphResponse,
  conceptId?: string,
): string[] {
  return graph.nodes
    .filter((node) => {
      if (conceptId !== undefined && node.id === conceptId) return false;
      return String(node.properties?.type ?? "").toLowerCase() !== "concept";
    })
    .map((node) => node.id);
}
