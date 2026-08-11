import { describe, expect, it } from "vitest";

import { toNvlGraph } from "./graph-encoding";
import { QUERY_FIXTURE_RESPONSE } from "./query-fixture";
import type { GraphNode, GraphRelationship } from "./types";

describe("query subgraph highlight (E9.2)", () => {
  it("highlights exactly the fixture subgraph nodes and dims the rest", () => {
    const nodes: GraphNode[] = [
      {
        id: "fact-a",
        caption: "Alice works at Acme Corp.",
        properties: { type: "fact", is_latest: true },
      },
      {
        id: "fact-b",
        caption: "Alice prefers remote work.",
        properties: { type: "preference", is_latest: true },
      },
      {
        id: "fact-c",
        caption: "Unrelated",
        properties: { type: "fact", is_latest: true },
      },
    ];
    const relationships: GraphRelationship[] = [
      {
        id: "r1",
        from: "fact-b",
        to: "fact-a",
        type: "EXTENDS",
      },
      {
        id: "r2",
        from: "fact-c",
        to: "fact-a",
        type: "EXTENDS",
      },
    ];

    const queryNodeIds = new Set(
      QUERY_FIXTURE_RESPONSE.subgraph.nodes.map((n) => n.id),
    );
    const queryRelKeys = new Set(
      QUERY_FIXTURE_RESPONSE.subgraph.relationships.map(
        (r) => `${r.source}->${r.target}:${r.type}`,
      ),
    );

    const { nodes: nvlNodes, rels } = toNvlGraph(nodes, relationships, {
      queryNodeIds,
      queryRelKeys,
    });

    const selected = nvlNodes.filter((n) => n.selected).map((n) => n.id).sort();
    expect(selected).toEqual(["fact-a", "fact-b"]);
    expect(nvlNodes.find((n) => n.id === "fact-c")?.color).toContain("rgba");

    const highlightedRel = rels.find((r) => r.id === "r1");
    const dimmedRel = rels.find((r) => r.id === "r2");
    expect(highlightedRel?.selected).toBe(true);
    expect(dimmedRel?.color).toContain("rgba");
  });

  it("fixture has clickable facts_used and a 2-node subgraph", () => {
    expect(QUERY_FIXTURE_RESPONSE.facts_used.length).toBeGreaterThanOrEqual(1);
    expect(QUERY_FIXTURE_RESPONSE.subgraph.nodes).toHaveLength(2);
    expect(QUERY_FIXTURE_RESPONSE.subgraph.relationships).toHaveLength(1);
  });
});
