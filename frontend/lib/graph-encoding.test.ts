import { describe, expect, it } from "vitest";

import {
  FACT_TYPE_COLORS,
  RELATION_COLORS,
  encodeNode,
  encodeRelationship,
  toNvlGraph,
} from "./graph-encoding";
import { FIXTURE_NODES, FIXTURE_RELATIONSHIPS } from "./graph-visual-fixture";

describe("graph visual encoding (§11.1)", () => {
  it("covers all fact types × is_latest states with distinct colors", () => {
    const encoded = FIXTURE_NODES.map((n) => encodeNode(n));
    const byId = Object.fromEntries(encoded.map((n) => [n.id, n]));

    expect(byId["fact-latest"].color).toBe(FACT_TYPE_COLORS.fact);
    expect(byId["pref-latest"].color).toBe(FACT_TYPE_COLORS.preference);
    expect(byId["ep-latest"].color).toBe(FACT_TYPE_COLORS.episode);

    expect(byId["fact-historical"].color).toContain("rgba");
    expect(byId["fact-historical"].caption).toContain("storico");
    expect(byId["pref-historical"].disabled).toBe(true);
    expect(byId["ep-historical"].disabled).toBe(true);
  });

  it("encodes UPDATES / EXTENDS / DERIVES with warning / info / success colors", () => {
    const updates = encodeRelationship(FIXTURE_RELATIONSHIPS[0]);
    const extendsRel = encodeRelationship(FIXTURE_RELATIONSHIPS[1]);
    const derives = encodeRelationship(FIXTURE_RELATIONSHIPS[2]);

    expect(updates.color).toBe(RELATION_COLORS.UPDATES);
    expect(extendsRel.color).toBe(RELATION_COLORS.EXTENDS);
    expect(derives.color).toBe(RELATION_COLORS.DERIVES);
    expect(derives.width).toBeLessThan(updates.width);
  });

  it("never includes Chunk nodes in NVL output", () => {
    const withChunk = [
      ...FIXTURE_NODES,
      {
        id: "chunk-should-not-appear",
        caption: "raw text",
        properties: { type: "fact", is_latest: true },
      },
    ];
    const { nodes } = toNvlGraph(withChunk, FIXTURE_RELATIONSHIPS);
    expect(nodes.some((n) => n.id.startsWith("chunk"))).toBe(false);
  });

  it("highlights history chain and dims the rest", () => {
    const historyIds = new Set(["fact-latest", "fact-historical"]);
    const historyRels = new Set(["rel-updates"]);
    const { nodes, rels } = toNvlGraph(FIXTURE_NODES, FIXTURE_RELATIONSHIPS, {
      historyNodeIds: historyIds,
      historyRelIds: historyRels,
    });
    const highlighted = nodes.filter((n) => n.selected);
    expect(highlighted.map((n) => n.id).sort()).toEqual(
      ["fact-historical", "fact-latest"].sort(),
    );
    expect(rels.find((r) => r.id === "rel-updates")?.selected).toBe(true);
  });
});
