/**
 * Collect node ids from a NodeQuery subgraph for GraphPanel highlightIds
 * (passed through as queryNodeIds — GraphPanel does not filter the graph).
 */

import type { NodeSubgraph } from "./types";

export function idsFromNodeQuerySubgraph(subgraph: NodeSubgraph): Set<string> {
  const ids = new Set<string>();
  for (const node of subgraph.nodes) {
    ids.add(node.id);
  }
  for (const rel of subgraph.relationships) {
    ids.add(rel.source);
    ids.add(rel.target);
  }
  return ids;
}
