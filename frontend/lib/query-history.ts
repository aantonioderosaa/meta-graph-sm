/**
 * Query history helpers (F4.5 / F4.6) — label formatting + detail load
 * without going through POST /query.
 */

import { getQueryLogDetail } from "./api-client";
import type { QueryHistoryEntry, QueryResponse } from "./types";

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

/** Load a past query snapshot — never calls POST /query. */
export function loadQueryFromHistory(id: string): Promise<QueryResponse> {
  return getQueryLogDetail(id);
}
