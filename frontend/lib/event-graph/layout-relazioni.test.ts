import { describe, expect, it } from "vitest";

import {
  RELAZIONI_ISOLATE_GAP,
  RELAZIONI_MIN_GAP,
  coseRelazioniLayoutOptions,
  positionsRelazioni,
  relazioniNodeLabel,
} from "./layout-relazioni";
import type { EventGraphElements } from "./types";

function evento(
  id: string,
  extra: Partial<{ posizione_doc: number; label: string }> = {},
) {
  return {
    data: {
      id,
      label: extra.label ?? id,
      tipo: "Fatto" as const,
      posizione_doc: extra.posizione_doc,
    },
  };
}

function minPairDistance(pos: Record<string, { x: number; y: number }>): number {
  const ids = Object.keys(pos);
  let min = Number.POSITIVE_INFINITY;
  for (let i = 0; i < ids.length; i += 1) {
    for (let j = i + 1; j < ids.length; j += 1) {
      const a = pos[ids[i]];
      const b = pos[ids[j]];
      min = Math.min(min, Math.hypot(a.x - b.x, a.y - b.y));
    }
  }
  return min;
}

describe("relazioniNodeLabel", () => {
  it("keeps short labels and ellipsizes long ones", () => {
    expect(relazioniNodeLabel("breve")).toBe("breve");
    const long = "Il Sole cominciò a brillare dolcemente e a scaldare l'aria.";
    const shortened = relazioniNodeLabel(long);
    expect(shortened.endsWith("…")).toBe(true);
    expect(shortened.length).toBeLessThan(long.length);
  });
});

describe("positionsRelazioni", () => {
  it("puts linked nodes on a circle, not a zigzag ladder", () => {
    const elements: EventGraphElements = {
      nodes: [
        evento("e0", { posizione_doc: 0 }),
        evento("e1", { posizione_doc: 1 }),
        evento("e2", { posizione_doc: 2 }),
      ],
      edges: [
        {
          data: {
            id: "c",
            source: "e0",
            target: "e2",
            tipo: "CAUSA",
          },
        },
        {
          data: {
            id: "k",
            source: "e1",
            target: "e2",
            tipo: "CONTRASTO",
          },
        },
      ],
    };
    const pos = positionsRelazioni(elements);
    expect(pos.e0).toBeDefined();
    expect(pos.e1).toBeDefined();
    expect(pos.e2).toBeDefined();
    expect(pos.e0).not.toEqual(pos.e1);
    expect(minPairDistance(pos)).toBeGreaterThanOrEqual(RELAZIONI_MIN_GAP - 1e-6);
    expect(RELAZIONI_MIN_GAP).toBeGreaterThanOrEqual(240);
    expect(RELAZIONI_ISOLATE_GAP).toBeGreaterThanOrEqual(240);
    const r0 = Math.hypot(pos.e0.x, pos.e0.y);
    const r1 = Math.hypot(pos.e1.x, pos.e1.y);
    const r2 = Math.hypot(pos.e2.x, pos.e2.y);
    expect(r0).toBeCloseTo(r1, 5);
    expect(r1).toBeCloseTo(r2, 5);
  });

  it("keeps isolated events off the circle so edges stay clear", () => {
    const elements: EventGraphElements = {
      nodes: [
        evento("a", { posizione_doc: 0 }),
        evento("b", { posizione_doc: 1 }),
        evento("lonely", { posizione_doc: 2 }),
      ],
      edges: [
        {
          data: {
            id: "c",
            source: "a",
            target: "b",
            tipo: "SCOPO",
          },
        },
      ],
    };
    const pos = positionsRelazioni(elements);
    expect(minPairDistance(pos)).toBeGreaterThanOrEqual(RELAZIONI_MIN_GAP - 1e-6);
    const circleY = Math.max(pos.a.y, pos.b.y);
    expect(pos.lonely.y).toBeGreaterThan(circleY);
    expect(Math.abs(pos.a.x - pos.b.x)).toBeLessThan(1e-6);
  });

  it("spaces a row of isolates without overlap", () => {
    const elements: EventGraphElements = {
      nodes: [
        evento("i0", { posizione_doc: 0 }),
        evento("i1", { posizione_doc: 1 }),
        evento("i2", { posizione_doc: 2 }),
      ],
      edges: [],
    };
    const pos = positionsRelazioni(elements);
    expect(pos.i1.x - pos.i0.x).toBeCloseTo(RELAZIONI_ISOLATE_GAP, 5);
    expect(minPairDistance(pos)).toBeGreaterThanOrEqual(RELAZIONI_MIN_GAP - 1e-6);
  });
});

describe("relazioni spacing", () => {
  it("keeps a readable gap between event nodes", () => {
    expect(RELAZIONI_MIN_GAP).toBeGreaterThanOrEqual(240);
    expect(RELAZIONI_ISOLATE_GAP).toBeGreaterThanOrEqual(240);
  });

  it("uses organic cose spacing, not a fixed circle", () => {
    const opts = coseRelazioniLayoutOptions(true);
    expect(opts.name).toBe("cose");
    expect(opts.randomize).toBe(true);
    expect(opts.idealEdgeLength).toBeTypeOf("function");
    expect((opts.idealEdgeLength as () => number)()).toBe(RELAZIONI_MIN_GAP);
    expect(opts.componentSpacing).toBe(RELAZIONI_ISOLATE_GAP);
  });
});
