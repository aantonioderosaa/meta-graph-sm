import { beforeEach, describe, expect, it } from "vitest";

import {
  clearEncodingCache,
  FACT_TYPE_COLORS,
  RELATION_COLORS,
  encodeNode,
  encodeRelationship,
  getEncodingCacheSize,
  toNvlGraph,
} from "./graph-encoding";
import { FIXTURE_NODES, FIXTURE_RELATIONSHIPS } from "./graph-visual-fixture";

describe("graph visual encoding (§11.1)", () => {
  beforeEach(() => {
    clearEncodingCache();
  });

  it("shows ● caption marker only when has_history is true (V1.2)", () => {
    const withHistory = encodeNode({
      id: "n1",
      caption: "Current fact",
      properties: { type: "fact", is_latest: true, has_history: true },
    });
    const without = encodeNode({
      id: "n2",
      caption: "Plain fact",
      properties: { type: "fact", is_latest: true, has_history: false },
    });
    const missing = encodeNode({
      id: "n3",
      caption: "No flag",
      properties: { type: "fact", is_latest: true },
    });

    expect(withHistory.caption.startsWith("● ")).toBe(true);
    expect(withHistory.color).toBe(FACT_TYPE_COLORS.fact);
    expect(without.caption.startsWith("●")).toBe(false);
    expect(missing.caption.startsWith("●")).toBe(false);
  });

  it("keeps type color and historical opacity when has_history is set (V1.2)", () => {
    const current = encodeNode({
      id: "c",
      caption: "Head",
      properties: { type: "preference", is_latest: true, has_history: true },
    });
    const historical = encodeNode({
      id: "h",
      caption: "Old",
      properties: { type: "preference", is_latest: false, has_history: true },
    });

    expect(current.color).toBe(FACT_TYPE_COLORS.preference);
    expect(current.caption).toMatch(/^● /);
    expect(historical.color).toContain("rgba");
    expect(historical.caption).toContain("storico");
    expect(historical.caption.startsWith("● ")).toBe(true);
  });

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

  it("crosses has_history with type / storico / pulse without regressing encoding (V1.3)", () => {
    const encoded = FIXTURE_NODES.map((n) => encodeNode(n));
    const byId = Object.fromEntries(encoded.map((n) => [n.id, n]));

    // has_history on current fact: badge + teal unchanged
    expect(byId["fact-latest"].caption.startsWith("● ")).toBe(true);
    expect(byId["fact-latest"].color).toBe(FACT_TYPE_COLORS.fact);

    // has_history on current preference: badge + amber unchanged
    expect(byId["pref-with-history"].caption.startsWith("● ")).toBe(true);
    expect(byId["pref-with-history"].color).toBe(FACT_TYPE_COLORS.preference);

    // has_history on historical episode: ● + (storico) + rgba
    expect(byId["ep-historical-with-history"].caption.startsWith("● ")).toBe(true);
    expect(byId["ep-historical-with-history"].caption).toContain("storico");
    expect(byId["ep-historical-with-history"].color).toContain("rgba");
    expect(byId["ep-historical-with-history"].disabled).toBe(true);

    // no badge when has_history is false
    expect(byId["pref-latest"].caption.startsWith("●")).toBe(false);
    expect(byId["fact-historical"].caption.startsWith("●")).toBe(false);

    // pulse still enlarges only that node; ● remains
    const pulsed = encodeNode(
      FIXTURE_NODES.find((n) => n.id === "fact-latest")!,
      { pulsing: true },
    );
    expect(pulsed.caption.startsWith("● ")).toBe(true);
    expect(pulsed.size).toBeGreaterThan(byId["fact-latest"].size);
    expect(pulsed.selected).toBe(true);
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

  it("reuses the same node object identity when visual state is unchanged", () => {
    const first = toNvlGraph(FIXTURE_NODES, FIXTURE_RELATIONSHIPS);
    const second = toNvlGraph(FIXTURE_NODES, FIXTURE_RELATIONSHIPS);

    expect(second.nodes).toHaveLength(first.nodes.length);
    for (let i = 0; i < first.nodes.length; i += 1) {
      expect(second.nodes[i]).toBe(first.nodes[i]);
    }
    for (let i = 0; i < first.rels.length; i += 1) {
      expect(second.rels[i]).toBe(first.rels[i]);
    }
  });

  it("replaces only the pulsing node object when pulse state changes", () => {
    const baseline = toNvlGraph(FIXTURE_NODES, FIXTURE_RELATIONSHIPS);
    const pulsed = toNvlGraph(FIXTURE_NODES, FIXTURE_RELATIONSHIPS, {
      pulsingIds: new Set(["fact-latest"]),
    });

    const baselineById = Object.fromEntries(baseline.nodes.map((n) => [n.id, n]));
    const pulsedById = Object.fromEntries(pulsed.nodes.map((n) => [n.id, n]));

    expect(pulsedById["fact-latest"]).not.toBe(baselineById["fact-latest"]);
    for (const id of Object.keys(baselineById)) {
      if (id === "fact-latest") continue;
      expect(pulsedById[id]).toBe(baselineById[id]);
    }
  });

  it("prunes encoding cache entries for removed ids", () => {
    toNvlGraph(FIXTURE_NODES, FIXTURE_RELATIONSHIPS);
    expect(getEncodingCacheSize().nodes).toBe(FIXTURE_NODES.length);
    expect(getEncodingCacheSize().rels).toBe(FIXTURE_RELATIONSHIPS.length);

    const subset = FIXTURE_NODES.filter((n) => n.id === "fact-latest");
    toNvlGraph(subset, []);
    expect(getEncodingCacheSize().nodes).toBe(1);
    expect(getEncodingCacheSize().rels).toBe(0);
  });
});
