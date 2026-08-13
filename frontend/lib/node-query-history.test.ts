import { beforeEach, describe, expect, it, vi } from "vitest";

import { loadNodeQueryFromHistory } from "./node-query-history";

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
