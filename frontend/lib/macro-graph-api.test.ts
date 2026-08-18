import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getGraphBundle,
  getMacroGraph,
  getNodeMetadata,
} from "./api-client";

function okResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

describe("macro graph API client paths", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(okResponse({ nodes: [], relationships: [] }));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getMacroGraph hits GET /graph/macro", async () => {
    await getMacroGraph();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/macro",
      expect.any(Object),
    );
  });

  it("getMacroGraph sends limit", async () => {
    await getMacroGraph({ limit: 50 });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/macro?limit=50",
      expect.any(Object),
    );
  });

  it("getGraphBundle hits GET /graph/bundle/{a}/{b}", async () => {
    fetchMock.mockResolvedValue(okResponse({ items: [] }));
    await getGraphBundle("a/1", "b 2");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/bundle/a%2F1/b%202",
      expect.any(Object),
    );
  });

  it("getNodeMetadata hits GET /graph/metadata/{id}", async () => {
    fetchMock.mockResolvedValue(
      okResponse({ id: "n1", kind: "node", name: "Alice" }),
    );
    await getNodeMetadata("n/1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/metadata/n%2F1",
      expect.any(Object),
    );
  });
});
