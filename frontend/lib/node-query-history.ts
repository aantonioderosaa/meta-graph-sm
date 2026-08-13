/**
 * Node-query history helpers — label formatting + detail load via
 * GET /graph/queries/{id} (never POST).
 */

import { getNodeQueryLogDetail } from "./api-client";
import type { NodeQueryResponse, QueryHistoryEntry } from "./types";

export function formatQueryHistoryLabel(
  entry: QueryHistoryEntry,
  maxLen = 40,
): string {
  const raw = entry.text.trim() || "(vuota)";
  const truncated =
    raw.length > maxLen ? `${raw.slice(0, Math.max(0, maxLen - 1))}…` : raw;
  let when = entry.created_at;
  try {
    const d = new Date(entry.created_at);
    if (!Number.isNaN(d.getTime())) {
      when = d.toLocaleString();
    }
  } catch {
    // keep raw created_at
  }
  return `${truncated} · ${when}`;
}

/** Load a past node-query snapshot — never calls POST /graph/query. */
export function loadNodeQueryFromHistory(
  id: string,
): Promise<NodeQueryResponse> {
  return getNodeQueryLogDetail(id);
}
