import { describe, expect, it } from "vitest";

import {
  QUERY_RELAZIONI,
  QUERY_TAB_ORDER,
  QUERY_TEMPI,
  QUERY_TRAVERSALS,
  buildEventQuerySpec,
  structuredQueryReady,
  traversalNeedsTarget,
} from "./query-spec";

describe("query-spec closed vocabs", () => {
  it("opens with natural language, then structured", () => {
    expect(QUERY_TAB_ORDER).toEqual(["nl", "structured"]);
  });

  it("includes current EventQuerySpec relation types (ancore + dizionario)", () => {
    expect(QUERY_RELAZIONI).toContain("APPARTIENE_A");
    expect(QUERY_RELAZIONI).toContain("SUCCESSIONE_ANCORA");
    expect(QUERY_RELAZIONI).toContain("SEQUENZA");
    expect(QUERY_RELAZIONI).toContain("CAUSA");
    expect(QUERY_RELAZIONI).not.toContain("PRECEDE");
    expect(QUERY_RELAZIONI).not.toContain("CONTEMPORANEO");
    expect(QUERY_RELAZIONI).not.toContain("SUCCESSIONE_ZONA");
  });

  it("includes trapassato among verbal tenses", () => {
    expect(QUERY_TEMPI).toContain("trapassato");
    expect(QUERY_TEMPI).toContain("non_finito");
  });

  it("keeps ancora-chain traversals, not PRECEDE", () => {
    expect(QUERY_TRAVERSALS).toEqual([
      "catena_di",
      "spina_dorsale_di",
      "prima_di",
      "dopo_di",
      "vicinato_temporale",
    ]);
  });
});

describe("buildEventQuerySpec", () => {
  it("sends testo (fulltext) and drops empty lemma/filters", () => {
    expect(
      buildEventQuerySpec(
        {
          testo: "vento",
          lemma: "",
          fonte: undefined,
        },
        "",
        "",
      ),
    ).toEqual({ testo: "vento" });
  });

  it("attaches finestra_tempo_assoluto only when a bound is set", () => {
    expect(buildEventQuerySpec({}, "1843", "")).toEqual({
      finestra_tempo_assoluto: { da: "1843" },
    });
    expect(buildEventQuerySpec({}, "", "1843-12-25")).toEqual({
      finestra_tempo_assoluto: { a: "1843-12-25" },
    });
    expect(buildEventQuerySpec({}, "", "")).toEqual({});
  });

  it("keeps traversal + target together", () => {
    expect(
      buildEventQuerySpec({
        traversal: "prima_di",
        traversal_target: "ev-1",
        tipo_relazione: "APPARTIENE_A",
      }),
    ).toEqual({
      traversal: "prima_di",
      traversal_target: "ev-1",
      tipo_relazione: "APPARTIENE_A",
    });
  });
});

describe("structuredQueryReady", () => {
  it("requires traversal_target only when a traversal is set", () => {
    expect(structuredQueryReady({})).toBe(true);
    expect(structuredQueryReady({ testo: "sole" })).toBe(true);
    expect(traversalNeedsTarget("dopo_di")).toBe(true);
    expect(structuredQueryReady({ traversal: "dopo_di" })).toBe(false);
    expect(
      structuredQueryReady({ traversal: "dopo_di", traversal_target: "ev-9" }),
    ).toBe(true);
  });
});
