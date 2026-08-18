import { describe, expect, it } from "vitest";

import {
  buildConceptTree,
  mergeConceptNeighbors,
  type ConceptTreeNode,
} from "./concept-tree";
import type { GraphResponse } from "./types";

const overview: GraphResponse = {
  nodes: [
    {
      id: "kernel",
      caption: "Agente",
      properties: { type: "concept", kernel_category: "Agente", definition: "chi agisce" },
    },
    {
      id: "genre",
      caption: "Aziende",
      properties: {
        type: "concept",
        kernel_category: "Agente",
        parent_uri: "kernel",
        definition: "organizzazioni",
      },
    },
    { id: "acme", caption: "Acme", properties: { type: "entity" } },
  ],
  relationships: [
    { id: "isa-1", from: "genre", to: "kernel", type: "IS_A", caption: "IS_A" },
    { id: "mo-1", from: "acme", to: "genre", type: "MEMBER_OF", caption: "MEMBER_OF" },
  ],
};

describe("buildConceptTree", () => {
  it("nests IS_A children under parents and attaches MEMBER_OF members", () => {
    const roots = buildConceptTree(overview);
    expect(roots).toHaveLength(1);
    expect(roots[0].id).toBe("kernel");
    expect(roots[0].kernel_category).toBe("Agente");
    expect(roots[0].definition).toBe("chi agisce");
    expect(roots[0].children).toHaveLength(1);
    expect(roots[0].children[0].id).toBe("genre");
    expect(roots[0].children[0].parent_uri).toBe("kernel");
    expect(roots[0].children[0].members.map((m) => m.id)).toEqual(["acme"]);
  });

  it("keeps concepts without IS_A as roots", () => {
    const graph: GraphResponse = {
      nodes: [
        { id: "a", caption: "A", properties: { type: "concept" } },
        { id: "b", caption: "B", properties: { type: "concept" } },
      ],
      relationships: [],
    };
    const roots = buildConceptTree(graph);
    expect(roots.map((r) => r.id).sort()).toEqual(["a", "b"]);
  });
});

describe("mergeConceptNeighbors", () => {
  it("adds IS_A children and members when expanding a concept", () => {
    const roots: ConceptTreeNode[] = [
      {
        id: "kernel",
        caption: "Agente",
        children: [],
        members: [],
      },
    ];
    const neighbors: GraphResponse = {
      nodes: [
        { id: "kernel", caption: "Agente", properties: { type: "concept" } },
        { id: "genre", caption: "Aziende", properties: { type: "concept" } },
        { id: "alice", caption: "Alice", properties: { type: "entity" } },
      ],
      relationships: [
        { id: "isa", from: "genre", to: "kernel", type: "IS_A" },
        { id: "mo", from: "alice", to: "kernel", type: "MEMBER_OF" },
      ],
    };
    const merged = mergeConceptNeighbors(roots, "kernel", neighbors);
    expect(merged[0].children.map((c) => c.id)).toEqual(["genre"]);
    expect(merged[0].members.map((m) => m.id)).toEqual(["alice"]);
    expect(roots[0].children).toHaveLength(0);
  });
});
