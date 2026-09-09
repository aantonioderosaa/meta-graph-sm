import { describe, expect, it } from "vitest";

import { CATENA_COLOR, DIZIONARIO_COLORS, PIANO_COLORS, encodeEdge, encodeNode } from "./encoding";
import { CATENA_COLOR } from "./encoding";
import {
  ARC_FAMIGLIE,
  EMPTY_STATS,
  LEGEND_DIM_OPACITY,
  countFor,
  elementMatches,
  filtersEqual,
  groupedArches,
  legendOpacity,
  swatchForArc,
  swatchForNode,
  swatchForTrait,
  toggleLegendFilter,
  traitEntries,
} from "./legend";
import type { CatalogArc, EventGraphCatalog, EventGraphStats } from "./types";

const precedeDizionario: CatalogArc = {
  tipo: "PRECEDE",
  famiglia: "dizionario",
  direzione: "Evento→Evento",
  significato: "ordine temporale da dizionario (connettivo)",
};

const precedeTemporale: CatalogArc = {
  tipo: "PRECEDE",
  famiglia: "temporale",
  direzione: "Evento→Evento",
  significato: "ordine cronologico",
};

const catalog: EventGraphCatalog = {
  nodes: [
    { id: "Evento", label: "Evento", shape: "pieno" },
    { id: "Menzione", label: "Menzione", shape: "ovale" },
    { id: "Quarantena", label: "Quarantena", shape: "tratteggiato" },
  ],
  traits: {
    tempo: ["presente", "passato"],
    polarita: ["affermata", "negata"],
    fattualita: ["FATTUALE", "IPOTETICO"],
    piano: ["PRIMO_PIANO", "SFONDO"],
    catena: {
      ruoli: ["STESSO_EVENTO", "AGGIORNA", "CONTRADDICE"],
      significato: {
        STESSO_EVENTO: "stessa occorrenza (vecchio→nuovo)",
        AGGIORNA: "aggiornamento dello stesso evento",
        CONTRADDICE: "contraddizione di polarità o fattualità",
      },
    },
  },
  arches: {
    argomentali: [
      {
        tipo: "SOGG",
        famiglia: "argomentali",
        direzione: "Evento→Menzione",
        significato: "soggetto dell'evento",
      },
      {
        tipo: "TEMPO",
        famiglia: "argomentali",
        direzione: "Evento→Menzione",
        significato: "circostanza temporale",
      },
    ],
    dizionario: [precedeDizionario],
    temporale: [precedeTemporale],
    placeholder: [
      {
        tipo: "COLLEGATO",
        famiglia: "placeholder",
        direzione: "Evento→Evento",
        significato: "segnale d'ordine debole",
      },
    ],
    struttura: [
      {
        tipo: "SATELLITE_DI",
        famiglia: "struttura",
        direzione: "Evento→Evento",
        significato: "SFONDO agganciato",
      },
    ],
  },
};

const stats: EventGraphStats = {
  nodi: { Evento: 4, Menzione: 7, Quarantena: 1 },
  archi: { PRECEDE: 5, SOGG: 3, TEMPO: 2 },
  tratti: { piano: { PRIMO_PIANO: 3, SFONDO: 1 } },
};

describe("groupedArches", () => {
  it("keeps PRECEDE in both dizionario and temporale", () => {
    const groups = groupedArches(catalog);
    expect(groups.map((g) => g.famiglia)).toEqual([...ARC_FAMIGLIE]);
    expect(groups.map((g) => g.famiglia)).not.toContain("catena");
    const dizionario = groups.find((g) => g.famiglia === "dizionario");
    const temporale = groups.find((g) => g.famiglia === "temporale");
    expect(dizionario?.arches.map((a) => a.tipo)).toEqual(["PRECEDE"]);
    expect(temporale?.arches.map((a) => a.tipo)).toEqual(["PRECEDE"]);
    expect(dizionario?.arches[0].significato).not.toBe(temporale?.arches[0].significato);
  });

  it("places catena in Nodi e tratti, not arc families", () => {
    expect(ARC_FAMIGLIE).not.toContain("catena");
    expect(catalog.arches.catena).toBeUndefined();
    expect(catalog.traits.catena).toEqual({
      ruoli: ["STESSO_EVENTO", "AGGIORNA", "CONTRADDICE"],
      significato: {
        STESSO_EVENTO: "stessa occorrenza (vecchio→nuovo)",
        AGGIORNA: "aggiornamento dello stesso evento",
        CONTRADDICE: "contraddizione di polarità o fattualità",
      },
    });
    expect(traitEntries(catalog.traits.catena).map((e) => e.value)).toEqual([
      "STESSO_EVENTO",
      "AGGIORNA",
      "CONTRADDICE",
    ]);
  });

  it("returns empty when catalog is missing", () => {
    expect(groupedArches(null)).toEqual([]);
    expect(groupedArches(undefined)).toEqual([]);
  });
});

describe("countFor", () => {
  it("looks up arcs, nodes, and piano traits", () => {
    expect(countFor(stats, { kind: "arco", key: "tipo", value: "PRECEDE" })).toBe(5);
    expect(countFor(stats, { kind: "nodo", key: "tipo", value: "Evento" })).toBe(4);
    expect(countFor(stats, { kind: "tratto", key: "piano", value: "PRIMO_PIANO" })).toBe(3);
  });

  it("returns 0 for missing buckets and empty stats", () => {
    expect(countFor(EMPTY_STATS, { kind: "arco", key: "tipo", value: "PRECEDE" })).toBe(0);
    expect(countFor(stats, { kind: "tratto", key: "tempo", value: "passato" })).toBe(0);
    expect(countFor(null, { kind: "nodo", key: "tipo", value: "Evento" })).toBe(0);
    expect(countFor(stats, null)).toBe(0);
  });
});

describe("elementMatches", () => {
  const precedeEdge = {
    data: { id: "p1", source: "e1", target: "e2", tipo: "PRECEDE" },
  };
  const tempoEdge = {
    data: { id: "t1", source: "e1", target: "m1", tipo: "TEMPO" },
  };
  const evento = {
    data: { id: "e1", label: "arrivare", tipo: "Evento", piano: "PRIMO_PIANO" },
  };
  const menzione = { data: { id: "m1", label: "Mario", tipo: "Menzione" } };

  it("matches PRECEDE edges by tipo regardless of famiglia", () => {
    const filter = { kind: "arco" as const, key: "tipo", value: "PRECEDE" };
    expect(elementMatches(precedeEdge, filter)).toBe(true);
    expect(elementMatches(tempoEdge, filter)).toBe(false);
    expect(elementMatches(evento, filter)).toBe(false);
  });

  it("does not treat node tipo as an arc tipo (overlap)", () => {
    const nodeFilter = { kind: "nodo" as const, key: "tipo", value: "Evento" };
    const arcFilter = { kind: "arco" as const, key: "tipo", value: "Evento" };
    expect(elementMatches(evento, nodeFilter)).toBe(true);
    expect(elementMatches(menzione, nodeFilter)).toBe(false);
    expect(elementMatches(precedeEdge, nodeFilter)).toBe(false);
    expect(elementMatches(evento, arcFilter)).toBe(false);
    expect(elementMatches(precedeEdge, arcFilter)).toBe(false);
  });

  it("matches traits on nodes only; TEMPO arc does not match tempo trait", () => {
    const pianoFilter = { kind: "tratto" as const, key: "piano", value: "PRIMO_PIANO" };
    const tempoTrait = { kind: "tratto" as const, key: "tempo", value: "passato" };
    expect(elementMatches(evento, pianoFilter)).toBe(true);
    expect(elementMatches(menzione, pianoFilter)).toBe(false);
    expect(elementMatches(precedeEdge, pianoFilter)).toBe(false);
    expect(
      elementMatches(
        { data: { id: "e2", tipo: "Evento", tempo: "passato" } },
        tempoTrait,
      ),
    ).toBe(true);
    expect(elementMatches(tempoEdge, tempoTrait)).toBe(false);
  });

  it("returns true for every element when filter is null", () => {
    expect(elementMatches(evento, null)).toBe(true);
    expect(elementMatches(precedeEdge, null)).toBe(true);
  });
});

describe("swatches", () => {
  it("uses encodeEdge for arc swatches including PRECEDE", () => {
    expect(swatchForArc("PRECEDE")).toBe(
      encodeEdge({ id: "PRECEDE", source: "s", target: "t", tipo: "PRECEDE" }).color,
    );
    expect(swatchForArc("PRECEDE")).toBe(DIZIONARIO_COLORS.PRECEDE);
    expect(swatchForArc("CAUSA")).toBe(
      encodeEdge({ id: "CAUSA", source: "s", target: "t", tipo: "CAUSA" }).color,
    );
  });

  it("uses encodeNode / PIANO_COLORS for nodes and piano traits", () => {
    expect(swatchForNode("Evento")).toBe(
      encodeNode({
        id: "Evento",
        label: "Evento",
        tipo: "Evento",
        piano: "PRIMO_PIANO",
        fattualita: "FATTUALE",
      }).color,
    );
    expect(swatchForTrait("piano", "PRIMO_PIANO")).toBe(PIANO_COLORS.PRIMO_PIANO);
    expect(swatchForTrait("piano", "SFONDO")).toBe(PIANO_COLORS.SFONDO);
    expect(swatchForTrait("catena", "AGGIORNA")).toBe(CATENA_COLOR);
  });
});

describe("legendOpacity and toggle", () => {
  it("dims non-matching elements and keeps matching opacity", () => {
    const filter = { kind: "arco" as const, key: "tipo", value: "PRECEDE" };
    const edge = { data: { id: "p", source: "a", target: "b", tipo: "PRECEDE" } };
    const other = { data: { id: "c", source: "a", target: "b", tipo: "CAUSA" } };
    expect(legendOpacity(1, edge, filter)).toBe(1);
    expect(legendOpacity(0.35, edge, filter)).toBe(0.35);
    expect(legendOpacity(1, other, filter)).toBe(LEGEND_DIM_OPACITY);
    expect(legendOpacity(1, other, null)).toBe(1);
  });

  it("toggles the same filter off", () => {
    const next = { kind: "nodo" as const, key: "tipo", value: "Evento" };
    expect(toggleLegendFilter(null, next)).toEqual(next);
    expect(toggleLegendFilter(next, next)).toBeNull();
    expect(filtersEqual(next, { kind: "nodo", key: "tipo", value: "Evento" })).toBe(true);
  });
});
