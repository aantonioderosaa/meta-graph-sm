import { describe, expect, it } from "vitest";

import {
  HIGHLIGHT_COLORS,
  idsFromQueryResult,
  mergeHighlights,
} from "./highlight";

describe("mergeHighlights", () => {
  it("marks disjoint ids as structured vs nl", () => {
    expect(mergeHighlights(["a", "b"], ["c"])).toEqual({
      a: "structured",
      b: "structured",
      c: "nl",
    });
  });

  it("marks overlap as both", () => {
    expect(mergeHighlights(["a", "b"], ["b", "c"])).toEqual({
      a: "structured",
      b: "both",
      c: "nl",
    });
  });

  it("returns empty for empty inputs", () => {
    expect(mergeHighlights([], [])).toEqual({});
    expect(mergeHighlights(["a"], [])).toEqual({ a: "structured" });
    expect(mergeHighlights([], ["b"])).toEqual({ b: "nl" });
  });

  it("exports three distinct overlay colors", () => {
    expect(HIGHLIGHT_COLORS.structured).toMatch(/^#/);
    expect(HIGHLIGHT_COLORS.nl).toMatch(/^#/);
    expect(HIGHLIGHT_COLORS.both).toMatch(/^#/);
    expect(
      new Set([
        HIGHLIGHT_COLORS.structured,
        HIGHLIGHT_COLORS.nl,
        HIGHLIGHT_COLORS.both,
      ]).size,
    ).toBe(3);
  });
});

describe("idsFromQueryResult", () => {
  it("collects evento ids and arco source/target when present", () => {
    expect(
      idsFromQueryResult({
        eventi: [{ id: "e1", lemma: "arrivare" }, { id: "e2" }],
        archi: [{ source: "e1", target: "e3" }, { tipo: "CAUSA" }],
      }),
    ).toEqual(["e1", "e2", "e1", "e3"]);
  });

  it("returns empty when risultato is missing", () => {
    expect(idsFromQueryResult(null)).toEqual([]);
    expect(idsFromQueryResult(undefined)).toEqual([]);
    expect(idsFromQueryResult({ eventi: [], archi: [] })).toEqual([]);
  });
});
