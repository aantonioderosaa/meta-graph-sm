import { describe, expect, it } from "vitest";

import { CATENA_COLOR, DIZIONARIO_COLORS, PIANO_COLORS, encodeEdge, encodeNode } from "./encoding";
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
import type { EventGraphCatalog, EventGraphStats } from "./types";

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
    dizionario: [
      {
        tipo: "CAUSA",
        famiglia: "dizionario",
        direzione: "Evento→Evento",
        significato: "relazione causale fra eventi",
      },
    ],
    temporale: [],
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
      {
        tipo: "SUCCESSIONE_ANCORA",
        famiglia: "struttura",
        direzione: "AncoraTemporale→AncoraTemporale",
        significato: "successione lineare fra ancore consecutive",
      },
    ],
  },
};

const stats: EventGraphStats = {
  nodi: { Evento: 4, Menzione: 7, Quarantena: 1 },
  archi: { CAUSA: 5, SOGG: 3, TEMPO: 2 },
  tratti: { piano: { PRIMO_PIANO: 3, SFONDO: 1 } },
};

describe("groupedArches", () => {
  it("omits empty temporale and does not list PRECEDE or CONTEMPORANEO", () => {
    const groups = groupedArches(catalog);
    expect(groups.map((g) => g.famiglia)).not.toContain("temporale");
    expect(groups.map((g) => g.famiglia)).not.toContain("catena");
    const tipi = groups.flatMap((g) => g.arches.map((a) => a.tipo));
    expect(tipi).not.toContain("PRECEDE");
    expect(tipi).not.toContain("CONTEMPORANEO");
    const dizionario = groups.find((g) => g.famiglia === "dizionario");
    const struttura = groups.find((g) => g.famiglia === "struttura");
    expect(dizionario?.arches.map((a) => a.tipo)).toEqual(["CAUSA"]);
    expect(struttura?.arches.map((a) => a.tipo)).toEqual([
      "SATELLITE_DI",
      "SUCCESSIONE_ANCORA",
    ]);
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
    expect(countFor(stats, { kind: "arco", key: "tipo", value: "CAUSA" })).toBe(5);
    expect(countFor(stats, { kind: "nodo", key: "tipo", value: "Evento" })).toBe(4);
    expect(countFor(stats, { kind: "tratto", key: "piano", value: "PRIMO_PIANO" })).toBe(3);
  });

  it("returns 0 for missing buckets and empty stats", () => {
    expect(countFor(EMPTY_STATS, { kind: "arco", key: "tipo", value: "CAUSA" })).toBe(0);
    expect(countFor(stats, { kind: "tratto", key: "tempo", value: "passato" })).toBe(0);
    expect(countFor(null, { kind: "nodo", key: "tipo", value: "Evento" })).toBe(0);
    expect(countFor(stats, null)).toBe(0);
  });
});

describe("elementMatches", () => {
  const causaEdge = {
    data: { id: "c1", source: "e1", target: "e2", tipo: "CAUSA" },
  };
  const tempoEdge = {
    data: { id: "t1", source: "e1", target: "m1", tipo: "TEMPO" },
  };
  const evento = {
    data: { id: "e1", label: "arrivare", tipo: "Evento", piano: "PRIMO_PIANO" },
  };
  const menzione = { data: { id: "m1", label: "Mario", tipo: "Menzione" } };

  it("matches CAUSA edges by tipo regardless of famiglia", () => {
    const filter = { kind: "arco" as const, key: "tipo", value: "CAUSA" };
    expect(elementMatches(causaEdge, filter)).toBe(true);
    expect(elementMatches(tempoEdge, filter)).toBe(false);
    expect(elementMatches(evento, filter)).toBe(false);
  });

  it("does not treat node tipo as an arc tipo (overlap)", () => {
    const nodeFilter = { kind: "nodo" as const, key: "tipo", value: "Evento" };
    const arcFilter = { kind: "arco" as const, key: "tipo", value: "Evento" };
    expect(elementMatches(evento, nodeFilter)).toBe(true);
    expect(elementMatches(menzione, nodeFilter)).toBe(false);
    expect(elementMatches(causaEdge, nodeFilter)).toBe(false);
    expect(elementMatches(evento, arcFilter)).toBe(false);
    expect(elementMatches(causaEdge, arcFilter)).toBe(false);
  });

  it("matches traits on nodes only; TEMPO arc does not match tempo trait", () => {
    const pianoFilter = { kind: "tratto" as const, key: "piano", value: "PRIMO_PIANO" };
    const tempoTrait = { kind: "tratto" as const, key: "tempo", value: "passato" };
    expect(elementMatches(evento, pianoFilter)).toBe(true);
    expect(elementMatches(menzione, pianoFilter)).toBe(false);
    expect(elementMatches(causaEdge, pianoFilter)).toBe(false);
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
    expect(elementMatches(causaEdge, null)).toBe(true);
  });
});

describe("swatches", () => {
  it("uses encodeEdge for arc swatches including CAUSA and SUCCESSIONE_ANCORA", () => {
    expect(swatchForArc("CAUSA")).toBe(
      encodeEdge({ id: "CAUSA", source: "s", target: "t", tipo: "CAUSA" }).color,
    );
    expect(swatchForArc("CAUSA")).toBe(DIZIONARIO_COLORS.CAUSA);
    expect(swatchForArc("SUCCESSIONE_ANCORA")).toBe(
      encodeEdge({
        id: "SUCCESSIONE_ANCORA",
        source: "s",
        target: "t",
        tipo: "SUCCESSIONE_ANCORA",
      }).color,
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
    const filter = { kind: "arco" as const, key: "tipo", value: "CAUSA" };
    const edge = { data: { id: "c", source: "a", target: "b", tipo: "CAUSA" } };
    const other = { data: { id: "s", source: "a", target: "b", tipo: "SEQUENZA" } };
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
