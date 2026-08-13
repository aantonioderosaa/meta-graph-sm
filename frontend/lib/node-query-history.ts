/**
 * Node-query history helpers — same QueryHistoryEntry labels as Fact queries,
 * detail load via GET /graph/queries/{id} (never POST).
 */

import { getNodeQueryLogDetail } from "./api-client";
import type { NodeQueryResponse } from "./types";

export { formatQueryHistoryLabel } from "./query-history";

/** Load a past node-query snapshot — never calls POST /graph/query or POST /query. */
export function loadNodeQueryFromHistory(
  id: string,
): Promise<NodeQueryResponse> {
  return getNodeQueryLogDetail(id);
}
