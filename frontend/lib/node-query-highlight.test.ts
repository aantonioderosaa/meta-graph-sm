import { beforeEach, describe, expect, it } from "vitest";

import { clearEncodingCache, toNvlGraph } from "./graph-encoding";
import { idsFromNodeQuerySubgraph } from "./node-query-highlight";
import type { GraphNode, NodeSubgraph } from "./types";

describe("idsFromNodeQuerySubgraph", () => {
  beforeEach(() => {
    clearEncodingCache();
  });

  it("collects subgraph node ids and relationship endpoints", () => {
    const subgraph: NodeSubgraph = {
      nodes: [
        { id: "alice", label: "Node", properties: { type: "entity" } },
        { id: "tech", label: "Concept", properties: {} },
      ],
      relationships: [
        { source: "alice", target: "summit", type: "PARTICIPATES" },
        { source: "summit", target: "tech", type: "HAS_CONCEPT" },
      ],
    };

    expect(idsFromNodeQuerySubgraph(subgraph)).toEqual(
      new Set(["alice", "tech", "summit"]),
    );
  });

  it("uses the Set as queryNodeIds without filtering the graph (GraphPanel)", () => {
    const subgraph: NodeSubgraph = {
      nodes: [{ id: "alice", label: "Node", properties: {} }],
      relationships: [{ source: "alice", target: "summit", type: "PARTICIPATES" }],
    };
    const queryNodeIds = idsFromNodeQuerySubgraph(subgraph);

    const graphNodes: GraphNode[] = [
      { id: "alice", caption: "Alice", properties: { type: "entity" } },
      { id: "summit", caption: "Summit", properties: { type: "event" } },
      { id: "other", caption: "Other", properties: { type: "entity" } },
    ];

    const { nodes } = toNvlGraph(graphNodes, [], { queryNodeIds });

    expect(nodes.map((n) => n.id).sort()).toEqual(["alice", "other", "summit"]);
    expect(nodes.find((n) => n.id === "other")?.color).toContain("rgba");
    expect(nodes.find((n) => n.id === "alice")?.selected).toBe(true);
  });
});
