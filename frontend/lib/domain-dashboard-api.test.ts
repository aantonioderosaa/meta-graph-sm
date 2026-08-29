import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getDomainChildrenGraph,
  getDomainDictionary,
  getDomainRules,
  getDomains,
  getDomainsGraph,
} from "./api-client";
import {
  currentDrillId,
  nodeTypeLabel,
  partitionDomainChildren,
  popDrill,
  pushDrill,
} from "./domain-nav";
import type { GraphNode } from "./types";

function okResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

describe("domain dashboard API client paths", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(okResponse({ items: [] }));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getDomains hits GET /graph/domains", async () => {
    await getDomains();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/domains",
      expect.any(Object),
    );
  });

  it("getDomainsGraph hits GET /graph/domains-graph", async () => {
    fetchMock.mockResolvedValue(okResponse({ nodes: [], relationships: [] }));
    await getDomainsGraph();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/domains-graph",
      expect.any(Object),
    );
  });

  it("getDomainDictionary hits GET /graph/domains/{id}/dictionary", async () => {
    await getDomainDictionary("sport/1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/domains/sport%2F1/dictionary",
      expect.any(Object),
    );
  });

  it("getDomainRules hits GET /graph/domains/{id}/rules", async () => {
    await getDomainRules("sport/1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/domains/sport%2F1/rules",
      expect.any(Object),
    );
  });

  it("getDomainChildrenGraph hits GET /graph/domains/{id}/children-graph", async () => {
    fetchMock.mockResolvedValue(okResponse({ nodes: [], relationships: [] }));
    await getDomainChildrenGraph("sport/1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/domains/sport%2F1/children-graph",
      expect.any(Object),
    );
  });
});

describe("domain drill stack", () => {
  it("pushDrill appends and ignores duplicates of the current scope", () => {
    expect(pushDrill([], "sport")).toEqual(["sport"]);
    expect(pushDrill(["sport"], "sport")).toEqual(["sport"]);
    expect(pushDrill(["sport"], "club")).toEqual(["sport", "club"]);
  });

  it("popDrill returns to the previous scope and no-ops at root", () => {
    expect(popDrill(["sport", "club"])).toEqual(["sport"]);
    expect(popDrill(["sport"])).toEqual([]);
    expect(popDrill([])).toEqual([]);
  });

  it("currentDrillId is null at root", () => {
    expect(currentDrillId([])).toBeNull();
    expect(currentDrillId(["a", "b"])).toBe("b");
  });

  it("partitionDomainChildren splits Concept vs Node leaves", () => {
    const nodes: GraphNode[] = [
      { id: "sport", caption: "Sport", properties: { type: "concept" } },
      { id: "alice", caption: "Alice", properties: { type: "entity" } },
      { id: "match", caption: "Match", properties: { type: "event" } },
    ];
    const { concepts, members } = partitionDomainChildren(nodes);
    expect(concepts.map((n) => n.id)).toEqual(["sport"]);
    expect(members.map((n) => n.id)).toEqual(["alice", "match"]);
  });

  it("nodeTypeLabel distinguishes entity and event", () => {
    expect(nodeTypeLabel("entity")).toBe("Entità");
    expect(nodeTypeLabel("event")).toBe("Evento");
    expect(nodeTypeLabel(null)).toBeNull();
  });
});
