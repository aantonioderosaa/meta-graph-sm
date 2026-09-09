import { describe, expect, it } from "vitest";

import {
  ARGOMENTALE_COLOR,
  COLLEGATO_COLOR,
  CONFLITTO_BORDER,
  DIZIONARIO_COLORS,
  PIANO_COLORS,
  encodeEdge,
  encodeNode,
} from "./encoding";

const evento = (
  piano: string,
  fattualita = "FATTUALE",
  id = "ev-1",
) => ({
  id,
  label: id,
  tipo: "Evento" as const,
  piano,
  fattualita,
});

describe("event-graph visual encoding", () => {
  it("colors nodes by piano: PRIMO_PIANO strong, SFONDO muted, FUORI_LINEA distinct", () => {
    const primo = encodeNode(evento("PRIMO_PIANO"));
    const sfondo = encodeNode(evento("SFONDO"));
    const fuori = encodeNode(evento("FUORI_LINEA"));

    expect(primo.color).toBe(PIANO_COLORS.PRIMO_PIANO);
    expect(sfondo.color).toBe(PIANO_COLORS.SFONDO);
    expect(fuori.color).toBe(PIANO_COLORS.FUORI_LINEA);
    expect(new Set([primo.color, sfondo.color, fuori.color]).size).toBe(3);
    expect(primo.color).not.toBe(sfondo.color);
    expect(fuori.color).not.toBe(primo.color);
    expect(fuori.color).not.toBe(sfondo.color);
  });

  it("desaturates when fattualita is not FATTUALE", () => {
    const fattuale = encodeNode(evento("PRIMO_PIANO", "FATTUALE"));
    const ipotetico = encodeNode(evento("PRIMO_PIANO", "IPOTETICO"));
    const nonFattuale = encodeNode(evento("SFONDO", "NON_FATTUALE"));

    expect(fattuale.desaturated).toBe(false);
    expect(ipotetico.desaturated).toBe(true);
    expect(nonFattuale.desaturated).toBe(true);
    expect(ipotetico.color).not.toBe(fattuale.color);
    expect(ipotetico.color).not.toBe(PIANO_COLORS.PRIMO_PIANO);
  });

  it("encodes Evento filled, Menzione ellipse/thin, Quarantena dashed", () => {
    const ev = encodeNode({
      id: "e",
      label: "arrivare",
      tipo: "Evento",
      piano: "PRIMO_PIANO",
      fattualita: "FATTUALE",
    });
    const men = encodeNode({
      id: "m",
      label: "Mario",
      tipo: "Menzione",
    });
    const qua = encodeNode({
      id: "q",
      label: "frammento",
      tipo: "Quarantena",
    });

    expect(ev.filled).toBe(true);
    expect(ev.shape).toBe("ellipse");
    expect(ev.borderStyle).toBe("solid");

    expect(men.shape).toBe("ellipse");
    expect(men.filled).toBe(false);
    expect(men.borderWidth).toBeLessThan(ev.borderWidth);
    expect(men.borderWidth).toBe(1);

    expect(qua.borderStyle).toBe("dashed");
    expect(qua.filled).toBe(false);
  });

  it("accepts cytoscape element wrappers { data }", () => {
    const encoded = encodeNode({
      data: evento("PRIMO_PIANO"),
    });
    expect(encoded.color).toBe(PIANO_COLORS.PRIMO_PIANO);
  });

  it("styles argomentali gray thin", () => {
    const edge = encodeEdge({
      id: "r1",
      source: "e",
      target: "m",
      tipo: "SOGG",
    });
    expect(edge.family).toBe("argomentali");
    expect(edge.color).toBe(ARGOMENTALE_COLOR);
    expect(edge.width).toBe(1);
    expect(edge.double).toBe(false);
  });

  it("styles dizionario edges colored by type", () => {
    const causa = encodeEdge({
      id: "c",
      source: "a",
      target: "b",
      tipo: "CAUSA",
    });
    const seq = encodeEdge({
      id: "s",
      source: "a",
      target: "b",
      tipo: "SEQUENZA",
    });
    expect(causa.family).toBe("dizionario");
    expect(causa.color).toBe(DIZIONARIO_COLORS.CAUSA);
    expect(seq.color).toBe(DIZIONARIO_COLORS.SEQUENZA);
    expect(causa.color).not.toBe(seq.color);
    expect(causa.width).toBeGreaterThan(1);
  });

  it("treats leftover chain-typed edges as unknown (no double stroke)", () => {
    for (const tipo of ["STESSO_EVENTO", "AGGIORNA", "CONTRADDICE"] as const) {
      const edge = encodeEdge({
        id: "k",
        source: "a",
        target: "b",
        tipo,
      });
      expect(edge.family).toBe("other");
      expect(edge.double).toBe(false);
    }
  });

  it("marks PRECEDE arrow; dato_esplicito solid, riferimento_testuale dashed", () => {
    const esplicito = encodeEdge({
      id: "p1",
      source: "a",
      target: "b",
      tipo: "PRECEDE",
      base: "dato_esplicito",
    });
    const testuale = encodeEdge({
      id: "p2",
      source: "a",
      target: "b",
      tipo: "PRECEDE",
      base: "riferimento_testuale",
    });
    expect(esplicito.family).toBe("temporale");
    expect(esplicito.markedArrow).toBe(true);
    expect(esplicito.lineStyle).toBe("solid");
    expect(testuale.markedArrow).toBe(true);
    expect(testuale.lineStyle).toBe("dashed");
    expect(esplicito.color).toBe(DIZIONARIO_COLORS.PRECEDE);
  });

  it("styles COLLEGATO ordine_* very light", () => {
    const edge = encodeEdge({
      id: "col",
      source: "a",
      target: "b",
      tipo: "COLLEGATO",
      segnale: "ordine_ingestione",
    });
    expect(edge.family).toBe("placeholder");
    expect(edge.color).toBe(COLLEGATO_COLOR);
    expect(edge.width).toBe(1);
  });

  it("makes superato_da semi-transparent and conflitto a red border", () => {
    const superseded = encodeEdge({
      id: "old",
      source: "a",
      target: "b",
      tipo: "COLLEGATO",
      segnale: "ordine_menzione",
      superato_da: "p-1",
    });
    const conflict = encodeEdge({
      id: "cf",
      source: "a",
      target: "b",
      tipo: "CAUSA",
      conflitto: true,
    });
    expect(superseded.opacity).toBeLessThan(1);
    expect(superseded.opacity).toBe(0.35);
    expect(conflict.borderColor).toBe(CONFLITTO_BORDER);
  });
});
