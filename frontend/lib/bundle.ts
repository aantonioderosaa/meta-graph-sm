/**
 * Undirected macro-bundle helpers (Fase 15). Caption is relation_count only.
 */

import type { GraphRelationship } from "./types";

export function bundleEdgeId(nodeAId: string, nodeBId: string): string {
  const [left, right] = [nodeAId, nodeBId].sort();
  return `bundle:${left}:${right}`;
}

export function parseBundleRelId(
  relId: string,
): { a: string; b: string } | null {
  const match = /^bundle:([^:]+):([^:]+)$/.exec(relId);
  if (!match) return null;
  return { a: match[1], b: match[2] };
}

export function endpointsFromBundleRel(
  rel: Pick<GraphRelationship, "id" | "from" | "to">,
): { a: string; b: string } {
  const parsed = parseBundleRelId(rel.id);
  if (parsed) return parsed;
  const [a, b] = [rel.from, rel.to].sort();
  return { a, b };
}

export function collapsePairCounts(
  pairs: Array<[string, string]>,
  macroIds: Set<string>,
  homes: Record<string, string>,
): Map<string, number> {
  const toMacro = (nid: string): string | null => {
    if (macroIds.has(nid)) return nid;
    const home = homes[nid];
    return home && macroIds.has(home) ? home : null;
  };
  const counts = new Map<string, number>();
  for (const [src, tgt] of pairs) {
    const left = toMacro(src);
    const right = toMacro(tgt);
    if (!left || !right || left === right) continue;
    const key = bundleEdgeId(left, right);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}
