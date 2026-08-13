import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getNodeQueryHistory,
  getNodeQueryLogDetail,
  postNodeQuery,
} from "./api-client";

const emptyNodeQuery = {
  answer: "ok",
  nodes_used: [],
  concepts_used: [],
  cited_node_ids: [],
  subgraph: { nodes: [], relationships: [] },
};

function okResponse(body: unknown = emptyNodeQuery) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

describe("node query API client paths", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(okResponse());
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("postNodeQuery POSTs to /graph/query with JSON {text}", async () => {
    await postNodeQuery({ text: "chi è Alice?" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/query",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ text: "chi è Alice?" }),
      }),
    );
  });

  it("getNodeQueryHistory GETs /graph/queries?limit=20", async () => {
    fetchMock.mockResolvedValue(okResponse({ items: [] }));
    await getNodeQueryHistory();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/queries?limit=20",
      expect.any(Object),
    );
  });

  it("getNodeQueryLogDetail encodes id in the path", async () => {
    await getNodeQueryLogDetail("q/1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/queries/q%2F1",
      expect.any(Object),
    );
  });

  it("does not hit the unprefixed /query endpoints", async () => {
    fetchMock.mockResolvedValueOnce(okResponse());
    fetchMock.mockResolvedValueOnce(okResponse({ items: [] }));
    fetchMock.mockResolvedValueOnce(okResponse());

    await postNodeQuery({ text: "x" });
    await getNodeQueryHistory(20);
    await getNodeQueryLogDetail("abc");

    const urls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(urls).not.toContain("http://localhost:8000/query");
    expect(urls.some((u) => /^https?:\/\/[^/]+\/queries/.test(u))).toBe(false);
    expect(urls).toEqual([
      "http://localhost:8000/graph/query",
      "http://localhost:8000/graph/queries?limit=20",
      "http://localhost:8000/graph/queries/abc",
    ]);
  });
});
