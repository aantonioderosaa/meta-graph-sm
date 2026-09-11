import { describe, expect, it } from "vitest";

import { sortClusterIds, timelineRankEdges } from "./layout-temporale";
import type { EventGraphNodeElement } from "./types";

function cluster(
  id: string,
  extra: Partial<EventGraphNodeElement["data"]> = {},
): EventGraphNodeElement {
  return {
    data: {
      id,
      label: String(extra.etichetta ?? extra.label ?? id),
      tipo: "ClusterTemporale",
      ...extra,
    },
  };
}

function evento(
  id: string,
  extra: Partial<EventGraphNodeElement["data"]> = {},
): EventGraphNodeElement {
  return {
    data: { id, label: id, tipo: "Evento", ...extra },
  };
}

function rankPairs(nodes: EventGraphNodeElement[]): [string, string][] {
  return timelineRankEdges(nodes).map((edge) => [
    edge.data.source,
    edge.data.target,
  ]);
}

describe("sortClusterIds", () => {
  // Piano riga 127–129 (MT8): ordinare per chiave_ordine, non parse ISO.
  // Sostituisce il test "orders three dated clusters 1990, 1994, 2001 ascending"
  // (etichetta ISO come chiave) — piano riga 41–42, 127–129.
  it("orders three dated clusters 1990, 1994, 2001 by chiave_ordine", () => {
    const nodes: EventGraphNodeElement[] = [
      cluster("c2001", { etichetta: "2001", tipo_cluster: "data_esplicita", chiave_ordine: 2001 }),
      evento("e-skip"),
      cluster("c1990", { etichetta: "1990", tipo_cluster: "data_esplicita", chiave_ordine: 1990 }),
      cluster("c1994", { etichetta: "1994", tipo_cluster: "data_esplicita", chiave_ordine: 1994 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["c1990", "c1994", "c2001"]);
  });

  // Piano riga 127–129 + bande MT6: datati prima degli ignoti.
  // Sostituisce "appends a simbolico with no reference after dated clusters"
  // (ex: parse ISO + id.localeCompare in coda) — piano riga 24–28, 127–129.
  it("appends a simbolico without chiave_ordine after dated clusters", () => {
    const nodes: EventGraphNodeElement[] = [
      cluster("c1994", { etichetta: "1994", tipo_cluster: "data_esplicita", chiave_ordine: 1994 }),
      cluster("c1990", { etichetta: "1990", tipo_cluster: "data_esplicita", chiave_ordine: 1990 }),
      cluster("sym", { etichetta: "il giorno del litigio", tipo_cluster: "simbolico" }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["c1990", "c1994", "sym"]);
  });

  // Piano riga 127–129: niente ancoraggio per menzione testuale.
  // Sostituisce "places a relative that references 1994 immediately after 1994"
  // (ancoraggio testuale) — piano riga 127–129, 24–28.
  it("places an undated relative in the narrative band, not after a mentioned year", () => {
    const nodes: EventGraphNodeElement[] = [
      cluster("c2001", { etichetta: "2001", tipo_cluster: "data_esplicita", chiave_ordine: 2001 }),
      cluster("rel", {
        etichetta: "dopo il 1994",
        tipo_cluster: "relativo",
        posizione_doc_min: 4,
      }),
      cluster("c1990", { etichetta: "1990", tipo_cluster: "data_esplicita", chiave_ordine: 1990 }),
      cluster("c1994", { etichetta: "1994", tipo_cluster: "data_esplicita", chiave_ordine: 1994 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["c1990", "c1994", "c2001", "rel"]);
  });

  it("orders dated clusters by chiave_ordine even when ids sort the other way", () => {
    const nodes: EventGraphNodeElement[] = [
      cluster("z", { chiave_ordine: 100 }),
      cluster("a", { chiave_ordine: 50 }),
      cluster("m", { chiave_ordine: 200 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["a", "z", "m"]);
  });

  // Piano riga 142–144: primo a sinistra = posizione_doc minima.
  it("orders undated clusters by posizione_doc_min so the leftmost has the smallest position", () => {
    const nodes: EventGraphNodeElement[] = [
      cluster("aaa-late", { posizione_doc_min: 40 }),
      cluster("zzz-early", { posizione_doc_min: 2 }),
      cluster("mmm-mid", { posizione_doc_min: 10 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["zzz-early", "mmm-mid", "aaa-late"]);
  });

  it("puts dated clusters before narrative-only clusters", () => {
    const nodes: EventGraphNodeElement[] = [
      cluster("narr-a", { posizione_doc_min: 1 }),
      cluster("dated-z", { chiave_ordine: 999 }),
      cluster("narr-b", { posizione_doc_min: 0 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["dated-z", "narr-b", "narr-a"]);
  });

  it("falls back to event posizione_doc on a legacy payload and does not sort by id", () => {
    const nodes: EventGraphNodeElement[] = [
      cluster("aaa", { etichetta: "Prova del Vento: soffio violento…" }),
      cluster("zzz", { etichetta: "L'uomo parte al mattino" }),
      evento("e-late", { parent: "aaa", posizione_doc: 80 }),
      evento("e-first", { parent: "zzz", posizione_doc: 3 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["zzz", "aaa"]);
  });

  it("uses ordine_vista when every cluster has it", () => {
    const nodes: EventGraphNodeElement[] = [
      cluster("later", { chiave_ordine: 1, ordine_vista: 2 }),
      cluster("first", { chiave_ordine: 9, ordine_vista: 0 }),
      cluster("mid", { chiave_ordine: 5, ordine_vista: 1 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["first", "mid", "later"]);
  });

  it("recomputes preorder when ordine_vista is missing, parent before children", () => {
    const nodes: EventGraphNodeElement[] = [
      cluster("child", { parent: "root", chiave_ordine: 20 }),
      cluster("root", { chiave_ordine: 10 }),
      cluster("other", { chiave_ordine: 30 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["root", "child", "other"]);
  });

  it("does not throw on malformed input", () => {
    expect(sortClusterIds(undefined as unknown as EventGraphNodeElement[])).toEqual([]);
    expect(sortClusterIds(null as unknown as EventGraphNodeElement[])).toEqual([]);
    expect(sortClusterIds([{} as EventGraphNodeElement, { data: null } as unknown as EventGraphNodeElement])).toEqual([]);
    expect(timelineRankEdges(undefined as unknown as EventGraphNodeElement[])).toEqual([]);
  });
});

describe("timelineRankEdges", () => {
  // Piano riga 131–132: rank edge solo fra fratelli, non padre→nipote.
  it("emits rank edges only between siblings of the same parent", () => {
    const nodes: EventGraphNodeElement[] = [
      cluster("A", { chiave_ordine: 10 }),
      cluster("A1", { parent: "A", chiave_ordine: 11 }),
      cluster("A2", { parent: "A", chiave_ordine: 12 }),
      cluster("B", { chiave_ordine: 20 }),
      cluster("B1", { parent: "B", chiave_ordine: 21 }),
    ];
    const pairs = rankPairs(nodes);
    expect(pairs).toEqual([
      ["A", "B"],
      ["A1", "A2"],
    ]);
    const asSet = new Set(pairs.map(([s, t]) => `${s}->${t}`));
    expect(asSet.has("A->A1")).toBe(false);
    expect(asSet.has("A2->B")).toBe(false);
    expect(asSet.has("A->B1")).toBe(false);
    expect(asSet.has("A2->B1")).toBe(false);
  });

  it("does not chain a parent to a grandchild across levels", () => {
    const nodes: EventGraphNodeElement[] = [
      cluster("nonno", { chiave_ordine: 1 }),
      cluster("figlio", { parent: "nonno", chiave_ordine: 2 }),
      cluster("nipote", { parent: "figlio", chiave_ordine: 3 }),
      cluster("zio", { chiave_ordine: 4 }),
    ];
    expect(rankPairs(nodes)).toEqual([["nonno", "zio"]]);
  });
});
