import { beforeEach, describe, expect, it } from "vitest";

import {
  clearEncodingCache,
  colorByKernelCategory,
  KERNEL_CATEGORY_COLORS,
  KERNEL_CATEGORY_FALLBACK,
  NODE_TYPE_COLORS,
  RELATION_COLORS,
  encodeNode,
  encodeRelationship,
  getEncodingCacheSize,
  toNvlGraph,
} from "./graph-encoding";
import { FIXTURE_NODES, FIXTURE_RELATIONSHIPS } from "./graph-visual-fixture";

describe("graph visual encoding", () => {
  beforeEach(() => {
    clearEncodingCache();
  });

  it("uses entity / event / concept colors and defaults unknown types to entity", () => {
    const encoded = FIXTURE_NODES.map((n) => encodeNode(n));
    const byId = Object.fromEntries(encoded.map((n) => [n.id, n]));

    expect(byId["entity-alice"].color).toBe(NODE_TYPE_COLORS.entity);
    expect(byId["event-summit"].color).toBe(NODE_TYPE_COLORS.event);
    expect(byId["concept-remote"].color).toBe(NODE_TYPE_COLORS.concept);

    const fallback = encodeNode({
      id: "unknown",
      caption: "Mystery",
      properties: { type: "unknown" },
    });
    expect(fallback.color).toBe(NODE_TYPE_COLORS.entity);

    const missingType = encodeNode({
      id: "no-type",
      caption: "Bare",
      properties: {},
    });
    expect(missingType.color).toBe(NODE_TYPE_COLORS.entity);
  });

  it("does not mark historical nodes or add (storico) captions", () => {
    const node = encodeNode({
      id: "n1",
      caption: "Alice",
      properties: { type: "entity", is_latest: false, has_history: true },
    });
    expect(node.caption).toBe("Alice");
    expect(node.caption).not.toContain("storico");
    expect(node.caption.startsWith("●")).toBe(false);
    expect(node.color).toBe(NODE_TYPE_COLORS.entity);
    expect(node.disabled).toBeUndefined();
  });

  it("encodes UPDATES / EXTENDS / PRECEDES / CAUSES / COOCCURS with dedicated colors", () => {
    const byId = Object.fromEntries(
      FIXTURE_RELATIONSHIPS.map((r) => [r.id, encodeRelationship(r)]),
    );

    expect(byId["rel-updates"].color).toBe(RELATION_COLORS.UPDATES);
    expect(byId["rel-extends"].color).toBe(RELATION_COLORS.EXTENDS);
    expect(byId["rel-precedes"].color).toBe(RELATION_COLORS.PRECEDES);
    expect(byId["rel-causes"].color).toBe(RELATION_COLORS.CAUSES);
    expect(byId["rel-cooccurs"].color).toBe(RELATION_COLORS.COOCCURS);
    expect(byId["rel-precedes"].color).not.toBe(RELATION_COLORS.EXTENDS);
    expect(byId["rel-causes"].color).not.toBe(RELATION_COLORS.EXTENDS);
    expect(byId["rel-cooccurs"].color).not.toBe(RELATION_COLORS.EXTENDS);
    expect(byId["rel-updates"].width).toBeGreaterThan(byId["rel-extends"].width);
  });

  it("never includes Chunk nodes in NVL output", () => {
    const withChunk = [
      ...FIXTURE_NODES,
      {
        id: "chunk-should-not-appear",
        caption: "raw text",
        properties: { type: "entity" },
      },
    ];
    const { nodes } = toNvlGraph(withChunk, FIXTURE_RELATIONSHIPS);
    expect(nodes.some((n) => n.id.startsWith("chunk"))).toBe(false);
  });

  it("highlights query nodes and dims the rest", () => {
    const queryNodeIds = new Set(["entity-alice", "event-summit"]);
    const queryRelKeys = new Set(["entity-alice->event-summit:participates"]);
    const { nodes, rels } = toNvlGraph(FIXTURE_NODES, FIXTURE_RELATIONSHIPS, {
      queryNodeIds,
      queryRelKeys,
    });
    const highlighted = nodes.filter((n) => n.selected);
    expect(highlighted.map((n) => n.id).sort()).toEqual(
      ["entity-alice", "event-summit"].sort(),
    );
    expect(nodes.find((n) => n.id === "entity-acme")?.color).toContain("rgba");
    expect(rels.find((r) => r.id === "rel-participates")?.selected).toBe(true);
    expect(rels.find((r) => r.id === "rel-extends")?.color).toContain("rgba");
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

  it("prunes encoding cache entries for removed ids", () => {
    toNvlGraph(FIXTURE_NODES, FIXTURE_RELATIONSHIPS);
    expect(getEncodingCacheSize().nodes).toBe(FIXTURE_NODES.length);
    expect(getEncodingCacheSize().rels).toBe(FIXTURE_RELATIONSHIPS.length);

    const subset = FIXTURE_NODES.filter((n) => n.id === "entity-alice");
    toNvlGraph(subset, []);
    expect(getEncodingCacheSize().nodes).toBe(1);
    expect(getEncodingCacheSize().rels).toBe(0);
  });

  it("truncates long captions at 48 chars", () => {
    const longName = "A".repeat(60);
    const encoded = encodeNode({
      id: "entity-long",
      caption: longName,
      properties: { type: "entity" },
    });
    expect(encoded.caption).toBe(`${"A".repeat(45)}…`);
  });

  it("colorByKernelCategory uses kernel map and leaves encodeNode type colors unchanged", () => {
    expect(colorByKernelCategory({
      id: "n1",
      caption: "Alice",
      properties: { type: "entity", kernel_category: "Agente" },
    })).toBe(KERNEL_CATEGORY_COLORS.Agente);
    expect(colorByKernelCategory({
      id: "n2",
      caption: "Roma",
      properties: { type: "entity", kernel_category: "Luogo" },
    })).toBe(KERNEL_CATEGORY_COLORS.Luogo);
    expect(colorByKernelCategory({
      id: "n3",
      caption: "X",
      properties: { type: "entity" },
    })).toBe(KERNEL_CATEGORY_FALLBACK);
    expect(colorByKernelCategory({
      id: "n4",
      caption: "Y",
      properties: { kernel_category: "Sconosciuto" },
    })).toBe(KERNEL_CATEGORY_FALLBACK);

    for (const name of [
      "Agente",
      "OggettoFisico",
      "Luogo",
      "Evento",
      "EntitaTemporale",
      "EntitaInformativa",
      "CostruttoSociale",
      "EntitaAstratta",
    ]) {
      expect(KERNEL_CATEGORY_COLORS[name]).toMatch(/^#[0-9A-Fa-f]{6}$/);
    }

    const entity = encodeNode({
      id: "e1",
      caption: "Alice",
      properties: { type: "entity", kernel_category: "Luogo" },
    });
    expect(entity.color).toBe(NODE_TYPE_COLORS.entity);
    const event = encodeNode({
      id: "ev1",
      caption: "Summit",
      properties: { type: "event", kernel_category: "Agente" },
    });
    expect(event.color).toBe(NODE_TYPE_COLORS.event);
    const concept = encodeNode({
      id: "c1",
      caption: "Sport",
      properties: { type: "concept", kernel_category: "Agente" },
    });
    expect(concept.color).toBe(NODE_TYPE_COLORS.concept);
  });
});
