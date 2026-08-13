import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  formatQueryHistoryLabel,
  loadNodeQueryFromHistory,
} from "./node-query-history";
import type { QueryHistoryEntry } from "./types";

const getNodeQueryLogDetail = vi.fn();
const postQuery = vi.fn();
const postNodeQuery = vi.fn();

vi.mock("./api-client", () => ({
  getNodeQueryLogDetail: (...args: unknown[]) => getNodeQueryLogDetail(...args),
  postQuery: (...args: unknown[]) => postQuery(...args),
  postNodeQuery: (...args: unknown[]) => postNodeQuery(...args),
}));

describe("node query history helpers", () => {
  beforeEach(() => {
    getNodeQueryLogDetail.mockReset();
    postQuery.mockReset();
    postNodeQuery.mockReset();
  });

  it("formats history options with truncated text", () => {
    const entry: QueryHistoryEntry = {
      id: "q1",
      text: "A".repeat(50),
      created_at: "2026-01-02T03:04:05Z",
    };
    const label = formatQueryHistoryLabel(entry, 40);
    expect(label.startsWith(`${"A".repeat(39)}…`)).toBe(true);
    expect(label).toContain("·");
  });

  it("loads history detail via getNodeQueryLogDetail, never postQuery or postNodeQuery", async () => {
    const snapshot = {
      answer: "Past node answer",
      nodes_used: [],
      concepts_used: [],
      cited_node_ids: [],
      subgraph: { nodes: [], relationships: [] },
    };
    getNodeQueryLogDetail.mockResolvedValue(snapshot);

    const result = await loadNodeQueryFromHistory("nq-past");

    expect(result).toEqual(snapshot);
    expect(getNodeQueryLogDetail).toHaveBeenCalledWith("nq-past");
    expect(postQuery).not.toHaveBeenCalled();
    expect(postNodeQuery).not.toHaveBeenCalled();
  });
});
