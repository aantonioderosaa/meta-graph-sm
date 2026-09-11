import { describe, expect, it } from "vitest";

import {
  dettaglioArcoFromElements,
  dettaglioNodoFromElements,
} from "./inspector";
import type { EventGraphElements } from "./types";

const elements: EventGraphElements = {
  nodes: [
    {
      data: {
        id: "ev-1",
        label: "soffiare",
        tipo: "Evento",
        piano: "PRIMO_PIANO",
      },
    },
    {
      data: {
        id: "ev-2",
        label: "cadere",
        tipo: "Evento",
        piano: "SFONDO",
      },
    },
  ],
  edges: [
    {
      data: {
        id: "CAUSA|ev-1|ev-2",
        source: "ev-1",
        target: "ev-2",
        tipo: "CAUSA",
        livello: "3",
        spiegazione: "il vento spinge",
      },
    },
  ],
};

describe("dettaglioArcoFromElements", () => {
  it("rebuilds dashboard payload from a visible edge without r.id", () => {
    const result = dettaglioArcoFromElements("CAUSA|ev-1|ev-2", elements);
    expect(result).not.toBeNull();
    expect(result?.tipo).toBe("CAUSA");
    expect(result?.proprieta.livello).toBe("3");
    expect(result?.proprieta.spiegazione).toBe("il vento spinge");
    expect(result?.source).toEqual({
      id: "ev-1",
      labels: ["Evento"],
      label: "soffiare",
    });
    expect(result?.target.label).toBe("cadere");
  });

  it("returns null when the edge is not in the current vista", () => {
    expect(dettaglioArcoFromElements("ghost", elements)).toBeNull();
    expect(dettaglioArcoFromElements("CAUSA|ev-1|ev-2", null)).toBeNull();
  });
});

describe("dettaglioNodoFromElements", () => {
  it("rebuilds dashboard payload from a visible node", () => {
    const result = dettaglioNodoFromElements("ev-1", elements);
    expect(result?.labels).toEqual(["Evento"]);
    expect(result?.proprieta.piano).toBe("PRIMO_PIANO");
    expect(result?.proprieta.id).toBeUndefined();
  });
});
