import { describe, expect, it } from "vitest";

import { idsFromConceptNeighbors } from "./concept-neighbors";
import type { GraphResponse } from "./types";

const neighbors: GraphResponse = {
  nodes: [
    { id: "c1", caption: "technology", properties: { type: "concept" } },
    { id: "e1", caption: "Alice", properties: { type: "entity" } },
    { id: "v1", caption: "Summit", properties: { type: "event" } },
  ],
  relationships: [],
};

describe("idsFromConceptNeighbors", () => {
  it("returns entity and event ids and excludes the concept node", () => {
    expect(idsFromConceptNeighbors(neighbors, "c1").sort()).toEqual([
      "e1",
      "v1",
    ]);
  });

  it("excludes any node typed as concept even without an explicit id", () => {
    expect(idsFromConceptNeighbors(neighbors).sort()).toEqual(["e1", "v1"]);
  });

  it("keeps neighbors that have no type property", () => {
    const graph: GraphResponse = {
      nodes: [
        { id: "c1", caption: "tech", properties: { type: "concept" } },
        { id: "orphan", caption: "?", properties: {} },
      ],
      relationships: [],
    };
    expect(idsFromConceptNeighbors(graph, "c1")).toEqual(["orphan"]);
  });
});
