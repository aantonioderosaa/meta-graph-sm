import { describe, expect, it } from "vitest";

import {
  ancoraDisplayLabel,
  filterTemporaleElements,
  isAncoraTemporaleNode,
  sortClusterIds,
  timelineRankEdges,
} from "./layout-temporale";
import type { EventGraphElements, EventGraphNodeElement } from "./types";

function ancora(
  id: string,
  extra: Partial<EventGraphNodeElement["data"]> = {},
): EventGraphNodeElement {
  return {
    data: {
      id,
      label: String(extra.etichetta ?? extra.label ?? id),
      tipo: "AncoraTemporale",
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
  it("orders three dated ancore 1990, 1994, 2001 by chiave_ordine", () => {
    const nodes: EventGraphNodeElement[] = [
      ancora("c2001", { etichetta: "2001", chiave_ordine: 2001 }),
      evento("e-skip"),
      ancora("c1990", { etichetta: "1990", chiave_ordine: 1990 }),
      ancora("c1994", { etichetta: "1994", chiave_ordine: 1994 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["c1990", "c1994", "c2001"]);
  });

  it("appends a simbolico without chiave_ordine after dated ancore", () => {
    const nodes: EventGraphNodeElement[] = [
      ancora("c1994", { etichetta: "1994", chiave_ordine: 1994 }),
      ancora("c1990", { etichetta: "1990", chiave_ordine: 1990 }),
      ancora("sym", { etichetta: "il giorno del litigio" }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["c1990", "c1994", "sym"]);
  });

  it("places an undated relative in the narrative band, not after a mentioned year", () => {
    const nodes: EventGraphNodeElement[] = [
      ancora("c2001", { etichetta: "2001", chiave_ordine: 2001 }),
      ancora("rel", {
        etichetta: "dopo il 1994",
        posizione_doc_min: 4,
      }),
      ancora("c1990", { etichetta: "1990", chiave_ordine: 1990 }),
      ancora("c1994", { etichetta: "1994", chiave_ordine: 1994 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["c1990", "c1994", "c2001", "rel"]);
  });

  it("orders dated ancore by chiave_ordine even when ids sort the other way", () => {
    const nodes: EventGraphNodeElement[] = [
      ancora("z", { chiave_ordine: 100 }),
      ancora("a", { chiave_ordine: 50 }),
      ancora("m", { chiave_ordine: 200 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["a", "z", "m"]);
  });

  it("orders undated ancore by posizione_doc_min so the leftmost has the smallest position", () => {
    const nodes: EventGraphNodeElement[] = [
      ancora("aaa-late", { posizione_doc_min: 40 }),
      ancora("zzz-early", { posizione_doc_min: 2 }),
      ancora("mmm-mid", { posizione_doc_min: 10 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["zzz-early", "mmm-mid", "aaa-late"]);
  });

  it("puts dated ancore before narrative-only ancore", () => {
    const nodes: EventGraphNodeElement[] = [
      ancora("narr-a", { posizione_doc_min: 1 }),
      ancora("dated-z", { chiave_ordine: 999 }),
      ancora("narr-b", { posizione_doc_min: 0 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["dated-z", "narr-b", "narr-a"]);
  });

  it("falls back to event posizione_doc on a legacy payload and does not sort by id", () => {
    const nodes: EventGraphNodeElement[] = [
      ancora("aaa", { etichetta: "Prova del Vento: soffio violento…" }),
      ancora("zzz", { etichetta: "L'uomo parte al mattino" }),
      evento("e-late", { parent: "aaa", posizione_doc: 80 }),
      evento("e-first", { parent: "zzz", posizione_doc: 3 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["zzz", "aaa"]);
  });

  it("falls back to event offset_inizio before posizione_doc and does not sort by id", () => {
    const nodes: EventGraphNodeElement[] = [
      ancora("aaa"),
      ancora("zzz"),
      evento("e-late", { parent: "aaa", posizione_doc: 0, offset_inizio: 80 }),
      evento("e-first", { parent: "zzz", posizione_doc: 0, offset_inizio: 3 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["zzz", "aaa"]);
  });

  it("uses ordine_vista when every ancora has it", () => {
    const nodes: EventGraphNodeElement[] = [
      ancora("later", { chiave_ordine: 1, ordine_vista: 2 }),
      ancora("first", { chiave_ordine: 9, ordine_vista: 0 }),
      ancora("mid", { chiave_ordine: 5, ordine_vista: 1 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["first", "mid", "later"]);
  });

  it("orders siblings by ordinale when present, even if chiave_ordine disagrees", () => {
    const nodes: EventGraphNodeElement[] = [
      ancora("later", { chiave_ordine: 1, ordinale: 2 }),
      ancora("first", { chiave_ordine: 9, ordinale: 0 }),
      ancora("mid", { chiave_ordine: 5, ordinale: 1 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["first", "mid", "later"]);
  });

  it("recomputes preorder when ordine_vista is missing, parent before children", () => {
    const nodes: EventGraphNodeElement[] = [
      ancora("child", { parent: "root", chiave_ordine: 20 }),
      ancora("root", { chiave_ordine: 10 }),
      ancora("other", { chiave_ordine: 30 }),
    ];
    expect(sortClusterIds(nodes)).toEqual(["root", "child", "other"]);
  });

  it("still orders leftover ClusterTemporale nodes", () => {
    const nodes: EventGraphNodeElement[] = [
      {
        data: {
          id: "old-b",
          label: "b",
          tipo: "ClusterTemporale",
          chiave_ordine: 20,
        },
      },
      {
        data: {
          id: "old-a",
          label: "a",
          tipo: "ClusterTemporale",
          chiave_ordine: 10,
        },
      },
    ];
    expect(sortClusterIds(nodes)).toEqual(["old-a", "old-b"]);
  });

  it("does not throw on malformed input", () => {
    expect(sortClusterIds(undefined as unknown as EventGraphNodeElement[])).toEqual([]);
    expect(sortClusterIds(null as unknown as EventGraphNodeElement[])).toEqual([]);
    expect(sortClusterIds([{} as EventGraphNodeElement, { data: null } as unknown as EventGraphNodeElement])).toEqual([]);
    expect(timelineRankEdges(undefined as unknown as EventGraphNodeElement[])).toEqual([]);
  });
});

describe("timelineRankEdges", () => {
  it("emits SUCCESSIONE_ANCORA only between siblings of the same parent, never PRECEDE", () => {
    const nodes: EventGraphNodeElement[] = [
      ancora("A", { chiave_ordine: 10 }),
      ancora("A1", { parent: "A", chiave_ordine: 11 }),
      ancora("A2", { parent: "A", chiave_ordine: 12 }),
      ancora("B", { chiave_ordine: 20 }),
      ancora("B1", { parent: "B", chiave_ordine: 21 }),
    ];
    const edges = timelineRankEdges(nodes);
    expect(edges.map((edge) => edge.data.tipo)).toEqual(
      edges.map(() => "SUCCESSIONE_ANCORA"),
    );
    expect(edges.some((edge) => String(edge.data.tipo) === "PRECEDE")).toBe(false);
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
      ancora("nonno", { chiave_ordine: 1 }),
      ancora("figlio", { parent: "nonno", chiave_ordine: 2 }),
      ancora("nipote", { parent: "figlio", chiave_ordine: 3 }),
      ancora("zio", { chiave_ordine: 4 }),
    ];
    expect(rankPairs(nodes)).toEqual([["nonno", "zio"]]);
  });
});

describe("ancoraDisplayLabel / isAncoraTemporaleNode", () => {
  it("prefers etichetta and ignores eventi", () => {
    expect(ancoraDisplayLabel(ancora("a1", { etichetta: "1843" }))).toBe("1843");
    expect(ancoraDisplayLabel(ancora("a1", { etichetta: "1843\u2060" }))).toBe("1843");
    expect(ancoraDisplayLabel(ancora("a1", { label: "anno" }))).toBe("anno");
    expect(isAncoraTemporaleNode(ancora("a1"))).toBe(true);
    expect(
      isAncoraTemporaleNode({
        data: { id: "old", label: "x", tipo: "ClusterTemporale" },
      }),
    ).toBe(true);
    expect(isAncoraTemporaleNode(evento("e1"))).toBe(false);
  });
});

describe("filterTemporaleElements", () => {
  const elements: EventGraphElements = {
    nodes: [
      ancora("root-a", { etichetta: "1843", ordinale: 0 }),
      ancora("root-b", { etichetta: "dopo", ordinale: 1 }),
      ancora("child-a1", { parent: "root-a", etichetta: "24 dic", ordinale: 0 }),
      ancora("child-a2", { parent: "root-a", etichetta: "25 dic", ordinale: 1 }),
      ancora("grand", { parent: "child-a1", etichetta: "sera" }),
      evento("e-leaf", { parent: "child-a1" }),
      evento("e-leaf-2", { parent: "child-a1" }),
      evento("e-deep", { parent: "grand" }),
      evento("e-root", { parent: "root-b" }),
    ],
    edges: [
      {
        data: {
          id: "sa-roots",
          source: "root-a",
          target: "root-b",
          tipo: "SUCCESSIONE_ANCORA",
        },
      },
      {
        data: {
          id: "sa-children",
          source: "child-a1",
          target: "child-a2",
          tipo: "SUCCESSIONE_ANCORA",
        },
      },
      {
        data: {
          id: "precede-leaf",
          source: "e-leaf",
          target: "e-leaf-2",
          tipo: "PRECEDE",
        },
      },
      {
        data: {
          id: "contemporaneo-deep",
          source: "e-leaf",
          target: "e-deep",
          tipo: "CONTEMPORANEO",
        },
      },
    ],
  };

  it("overview keeps only root ancore and SUCCESSIONE_ANCORA among them", () => {
    const filtered = filterTemporaleElements(elements, []);
    expect(filtered.nodes.map((node) => node.data.id).sort()).toEqual([
      "root-a",
      "root-b",
    ]);
    expect(filtered.nodes.every((node) => node.data.parent == null)).toBe(true);
    expect(filtered.edges.map((edge) => edge.data.id)).toEqual(["sa-roots"]);
  });

  it("empty or missing path is the overview", () => {
    expect(filterTemporaleElements(elements, null).nodes.map((n) => n.data.id).sort()).toEqual([
      "root-a",
      "root-b",
    ]);
    expect(filterTemporaleElements(elements, undefined).edges.map((e) => e.data.id)).toEqual([
      "sa-roots",
    ]);
  });

  it("drill on a root shows that ancora, its children, not grandchildren or nested events", () => {
    const filtered = filterTemporaleElements(elements, ["root-a"]);
    expect(filtered.nodes.map((node) => node.data.id).sort()).toEqual([
      "child-a1",
      "child-a2",
      "root-a",
    ]);
    expect(filtered.edges.map((edge) => edge.data.id)).toEqual(["sa-children"]);
    const child = filtered.nodes.find((node) => node.data.id === "child-a1");
    expect(child?.data.parent).toBe("root-a");
    const root = filtered.nodes.find((node) => node.data.id === "root-a");
    expect(root?.data.parent).toBeUndefined();
  });

  it("deeper path shows the focused ancora plus events; leftover PRECEDE is dropped", () => {
    const filtered = filterTemporaleElements(elements, ["root-a", "child-a1"]);
    expect(filtered.nodes.map((node) => node.data.id).sort()).toEqual([
      "child-a1",
      "e-leaf",
      "e-leaf-2",
      "grand",
    ]);
    expect(filtered.edges.map((edge) => edge.data.id).sort()).toEqual([]);
    expect(filtered.edges.some((edge) => edge.data.tipo === "PRECEDE")).toBe(false);
    expect(filtered.edges.some((edge) => edge.data.tipo === "CONTEMPORANEO")).toBe(
      false,
    );
  });

  it("lists events inside a box by offset_inizio, not id, when posizione_doc is the same", () => {
    const boxed: EventGraphElements = {
      nodes: [
        ancora("leaf", { etichetta: "sera" }),
        evento("zzz-late", {
          parent: "leaf",
          posizione_doc: 0,
          posizione_chunk: 1,
          offset_inizio: 90,
        }),
        evento("aaa-early", {
          parent: "leaf",
          posizione_doc: 0,
          posizione_chunk: 0,
          offset_inizio: 10,
        }),
      ],
      edges: [],
    };
    const filtered = filterTemporaleElements(boxed, ["leaf"]);
    expect(
      filtered.nodes.filter((node) => node.data.tipo === "Evento").map((node) => node.data.id),
    ).toEqual(["aaa-early", "zzz-late"]);
  });

  it("truncates a stale path to the longest valid prefix (breadcrumb)", () => {
    const stale = filterTemporaleElements(elements, ["root-a", "missing", "grand"]);
    expect(stale.nodes.map((node) => node.data.id).sort()).toEqual([
      "child-a1",
      "child-a2",
      "root-a",
    ]);
    const skippedParent = filterTemporaleElements(elements, ["root-a", "grand"]);
    expect(skippedParent.nodes.map((node) => node.data.id).sort()).toEqual([
      "child-a1",
      "child-a2",
      "root-a",
    ]);
  });

  it("unknown focus falls back to the overview", () => {
    const filtered = filterTemporaleElements(elements, ["missing"]);
    expect(filtered.nodes.map((node) => node.data.id).sort()).toEqual([
      "root-a",
      "root-b",
    ]);
    expect(filtered.edges.map((edge) => edge.data.id)).toEqual(["sa-roots"]);
  });

  it("focused leaf ancora draws no event-to-event temporal edges", () => {
    const filtered = filterTemporaleElements(elements, ["root-b"]);
    expect(filtered.nodes.map((node) => node.data.id).sort()).toEqual([
      "e-root",
      "root-b",
    ]);
    expect(filtered.edges).toEqual([]);
    expect(filtered.edges.some((edge) => edge.data.tipo === "PRECEDE")).toBe(false);
    expect(filtered.edges.some((edge) => edge.data.tipo === "CONTEMPORANEO")).toBe(
      false,
    );
  });

  it("treats leftover ClusterTemporale hubs as roots in the overview", () => {
    const leftover: EventGraphElements = {
      nodes: [
        {
          data: {
            id: "old-a",
            label: "1990",
            tipo: "ClusterTemporale",
          },
        },
        {
          data: {
            id: "old-b",
            label: "1994",
            tipo: "ClusterTemporale",
          },
        },
        evento("hidden", { parent: "old-a" }),
      ],
      edges: [
        {
          data: {
            id: "sa-old",
            source: "old-a",
            target: "old-b",
            tipo: "SUCCESSIONE_ANCORA",
          },
        },
      ],
    };
    const filtered = filterTemporaleElements(leftover, []);
    expect(filtered.nodes.map((node) => node.data.id).sort()).toEqual([
      "old-a",
      "old-b",
    ]);
    expect(filtered.edges.map((edge) => edge.data.id)).toEqual(["sa-old"]);
  });
});
