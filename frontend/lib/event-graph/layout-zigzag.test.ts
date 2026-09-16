import { describe, expect, it } from "vitest";

import type { EventGraphElements } from "./types";
import {
  ZIGZAG_H_GAP,
  ZIGZAG_V_GAP,
  chiaveEsposizione,
  ladderEdgeLabel,
  orderedEventoIds,
  positionsZigzag,
} from "./layout-zigzag";

function evento(
  id: string,
  extra: Partial<{
    posizione_doc: number;
    posizione_chunk: number;
    offset_inizio: number;
  }> = {},
) {
  return {
    data: {
      id,
      label: id,
      tipo: "Evento" as const,
      ...extra,
    },
  };
}

describe("chiaveEsposizione / orderedEventoIds", () => {
  it("sorts by posizione_doc, then chunk, then offset, then id", () => {
    const a = evento("late", { posizione_doc: 2, posizione_chunk: 0 }).data;
    const b = evento("early", { posizione_doc: 0, posizione_chunk: 9 }).data;
    const c = evento("mid-b", {
      posizione_doc: 1,
      posizione_chunk: 0,
      offset_inizio: 20,
    }).data;
    const d = evento("mid-a", {
      posizione_doc: 1,
      posizione_chunk: 0,
      offset_inizio: 10,
    }).data;
    expect(chiaveEsposizione(b)[0]).toBeLessThan(chiaveEsposizione(a)[0]);
    const elements: EventGraphElements = {
      nodes: [
        { data: a },
        { data: c },
        { data: d },
        { data: b },
      ],
      edges: [],
    };
    expect(orderedEventoIds(elements)).toEqual([
      "early",
      "mid-a",
      "mid-b",
      "late",
    ]);
  });

  it("walks SEQUENZA when no exposition fields are present", () => {
    const elements: EventGraphElements = {
      nodes: [evento("c"), evento("a"), evento("b")],
      edges: [
        { data: { id: "s1", source: "c", target: "a", tipo: "SEQUENZA" } },
        { data: { id: "s2", source: "a", target: "b", tipo: "SEQUENZA" } },
      ],
    };
    expect(orderedEventoIds(elements)).toEqual(["c", "a", "b"]);
  });
});

describe("positionsZigzag", () => {
  it("alternates left/right columns and increases y down the chain", () => {
    const elements: EventGraphElements = {
      nodes: [
        evento("e0", { posizione_doc: 0 }),
        evento("e1", { posizione_doc: 1 }),
        evento("e2", { posizione_doc: 2 }),
        evento("e3", { posizione_doc: 3 }),
      ],
      edges: [],
    };
    const pos = positionsZigzag(elements);
    expect(pos.e0).toEqual({ x: 0, y: 0 });
    expect(pos.e1).toEqual({ x: ZIGZAG_H_GAP, y: ZIGZAG_V_GAP });
    expect(pos.e2).toEqual({ x: 0, y: 2 * ZIGZAG_V_GAP });
    expect(pos.e3).toEqual({ x: ZIGZAG_H_GAP, y: 3 * ZIGZAG_V_GAP });
  });

  it("translates the ladder from an origin", () => {
    const elements: EventGraphElements = {
      nodes: [evento("e0", { posizione_chunk: 0 })],
      edges: [],
    };
    const pos = positionsZigzag(elements, { x: 100, y: 50 });
    expect(pos.e0).toEqual({ x: 100, y: 50 });
  });

  it("places a menzione outside the column of its event", () => {
    const elements: EventGraphElements = {
      nodes: [
        evento("e0", { posizione_doc: 0 }),
        { data: { id: "m0", label: "Mario", tipo: "Menzione" } },
      ],
      edges: [
        { data: { id: "sogg", source: "e0", target: "m0", tipo: "SOGG" } },
      ],
    };
    const pos = positionsZigzag(elements);
    expect(pos.m0.x).toBeLessThan(pos.e0.x);
    expect(pos.m0.y).toBe(pos.e0.y);
  });
});

describe("ladderEdgeLabel", () => {
  it("shows dizionario/temporale tipi and hides argument edges", () => {
    expect(ladderEdgeLabel("CONTRASTO")).toBe("CONTRASTO");
    expect(ladderEdgeLabel("SEQUENZA")).toBe("SEQUENZA");
    expect(ladderEdgeLabel("PRECEDE")).toBe("PRECEDE");
    expect(ladderEdgeLabel("SOGG")).toBe("");
    expect(ladderEdgeLabel("COLLEGATO")).toBe("");
  });
});
