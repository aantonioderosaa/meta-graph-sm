import { describe, expect, it } from "vitest";

import { positionsOrdine } from "./layout-ordine";
import type { EventGraphElements } from "./types";

function zona(id: string, ordinale: number) {
  return { data: { id, label: id, tipo: "Zona" as const, ordinale } };
}

function evento(id: string, parent: string) {
  return { data: { id, label: id, tipo: "Evento" as const, parent } };
}

describe("positionsOrdine", () => {
  it("places 5 Zona hubs on y=0 with x strictly increasing by ordinale", () => {
    const elements: EventGraphElements = {
      nodes: [0, 1, 2, 3, 4].map((ordinale) => zona(`z${ordinale}`, ordinale)),
      edges: [],
    };
    const pos = positionsOrdine(elements);
    const hubs = [0, 1, 2, 3, 4].map((ordinale) => pos[`z${ordinale}`]);
    expect(hubs.every((p) => p && p.y === 0)).toBe(true);
    for (let i = 1; i < hubs.length; i += 1) {
      expect(hubs[i].x).toBeGreaterThan(hubs[i - 1].x);
    }
  });

  it("stacks children of even zonas above (y<0) and odd zonas below (y>0)", () => {
    const elements: EventGraphElements = {
      nodes: [
        zona("z0", 0),
        zona("z1", 1),
        evento("e0a", "z0"),
        evento("e0b", "z0"),
        evento("e1a", "z1"),
        evento("e1b", "z1"),
      ],
      edges: [],
    };
    const pos = positionsOrdine(elements);
    expect(pos.e0a.y).toBeLessThan(0);
    expect(pos.e0b.y).toBeLessThan(0);
    expect(pos.e1a.y).toBeGreaterThan(0);
    expect(pos.e1b.y).toBeGreaterThan(0);
  });

  it("aligns children of the same zona on the hub x", () => {
    const elements: EventGraphElements = {
      nodes: [
        zona("z2", 2),
        zona("z3", 3),
        evento("e2a", "z2"),
        evento("e2b", "z2"),
        evento("e3a", "z3"),
      ],
      edges: [],
    };
    const pos = positionsOrdine(elements);
    expect(pos.e2a.x).toBe(pos.z2.x);
    expect(pos.e2b.x).toBe(pos.z2.x);
    expect(pos.e3a.x).toBe(pos.z3.x);
  });

  it("treats missing ordinale as 0 and skips unknown parent ids", () => {
    const elements: EventGraphElements = {
      nodes: [
        { data: { id: "z-missing", label: "z", tipo: "Zona" } },
        evento("orphan", "no-such-zona"),
      ],
      edges: [],
    };
    const pos = positionsOrdine(elements);
    expect(pos["z-missing"]).toEqual({ x: 0, y: 0 });
    expect(pos.orphan).toBeUndefined();
  });
});
