import { describe, expect, it } from "vitest";

import {
  ARGOMENTALE_COLOR,
  CLUSTER_TEMPORALE_COLOR,
  COLLEGATO_COLOR,
  CONFLITTO_BORDER,
  CONTEMPORANEO_COLOR,
  DIZIONARIO_COLORS,
  PIANO_COLORS,
  SUCCESSIONE_ZONA_COLOR,
  ZONA_COLOR,
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

  it("encodes Zona as a distinct round-rectangle hub, not Evento piano colors", () => {
    const zona = encodeNode({
      id: "z0",
      label: "zona 0",
      tipo: "Zona",
      piano: "PRIMO_PIANO",
      ordinale: 0,
    });
    const eventoPiano = encodeNode(evento("PRIMO_PIANO"));
    expect(zona.shape).toBe("round-rectangle");
    expect(zona.color).toBe(ZONA_COLOR);
    expect(zona.color).not.toBe(eventoPiano.color);
    expect(zona.color).not.toBe(PIANO_COLORS.PRIMO_PIANO);
    expect(zona.filled).toBe(true);
  });

  it("encodes ClusterTemporale as a filled round-rectangle distinct from Zona", () => {
    const cluster = encodeNode({
      id: "cl-1",
      label: "1994",
      tipo: "ClusterTemporale",
      etichetta: "1994",
      tipo_cluster: "data_esplicita",
    });
    const zona = encodeNode({
      id: "z0",
      label: "zona 0",
      tipo: "Zona",
      ordinale: 0,
    });
    expect(cluster.shape).toBe("round-rectangle");
    expect(cluster.filled).toBe(true);
    expect(cluster.color).toBe(CLUSTER_TEMPORALE_COLOR);
    expect(cluster.color).not.toBe(zona.color);
    expect(cluster.color).not.toBe(ZONA_COLOR);
    expect(cluster.color).not.toBe(PIANO_COLORS.PRIMO_PIANO);
    expect(cluster.borderStyle).toBe("solid");
  });

  it("dashes ClusterTemporale border when stimato is truthy; absent stays solid", () => {
    const stimato = encodeNode({
      id: "cl-st",
      label: "sera",
      tipo: "ClusterTemporale",
      etichetta: "sera",
      stimato: true,
    });
    const stimatoStr = encodeNode({
      id: "cl-st-s",
      label: "sera",
      tipo: "ClusterTemporale",
      stimato: "true",
    });
    const nonStimato = encodeNode({
      id: "cl-ok",
      label: "1843",
      tipo: "ClusterTemporale",
      stimato: false,
    });
    const assente = encodeNode({
      id: "cl-abs",
      label: "1843",
      tipo: "ClusterTemporale",
    });
    expect(stimato.borderStyle).toBe("dashed");
    expect(stimatoStr.borderStyle).toBe("dashed");
    expect(nonStimato.borderStyle).toBe("solid");
    expect(assente.borderStyle).toBe("solid");
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

    const quaStimato = encodeNode({
      id: "q2",
      label: "frammento",
      tipo: "Quarantena",
      stimato: false,
    });
    expect(quaStimato.borderStyle).toBe("dashed");
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

  it("treats CONTEMPORANEO as temporale family", () => {
    // Piano 38–40, 136–137: stesso family, stile distinto da PRECEDE
    // (niente punta, dashed, colore proprio — non #0D9488).
    const edge = encodeEdge({
      id: "ct",
      source: "a",
      target: "b",
      tipo: "CONTEMPORANEO",
    });
    const precede = encodeEdge({
      id: "p",
      source: "a",
      target: "b",
      tipo: "PRECEDE",
    });
    expect(edge.family).toBe("temporale");
    expect(edge.color).toBe(CONTEMPORANEO_COLOR);
    expect(edge.color).not.toBe(DIZIONARIO_COLORS.PRECEDE);
    expect(edge.color).not.toBe(precede.color);
    expect(edge.color).not.toBe(DIZIONARIO_COLORS.CAUSA);
    expect(edge.color).not.toBe(DIZIONARIO_COLORS.SEQUENZA);
    expect(edge.lineStyle).toBe("dashed");
    expect(edge.markedArrow).toBe(false);
    expect(precede.markedArrow).toBe(true);
    expect(precede.color).toBe(DIZIONARIO_COLORS.PRECEDE);
    expect(precede.lineStyle).toBe("solid");
  });

  it("does not change dizionario color when spiegazione is present", () => {
    const plain = encodeEdge({
      id: "c",
      source: "a",
      target: "b",
      tipo: "CAUSA",
    });
    const withSpieg = encodeEdge({
      id: "c2",
      source: "a",
      target: "b",
      tipo: "CAUSA",
      spiegazione: "l'arrivo provoca la partenza",
    });
    expect(withSpieg.family).toBe("dizionario");
    expect(withSpieg.color).toBe(plain.color);
    expect(withSpieg.color).toBe(DIZIONARIO_COLORS.CAUSA);
  });

  it("styles SUCCESSIONE_ZONA as struttura, not CAUSA or SEQUENZA", () => {
    const edge = encodeEdge({
      id: "sz",
      source: "z0",
      target: "z1",
      tipo: "SUCCESSIONE_ZONA",
      riassunto_transizione: "cambia il luogo",
    });
    const seq = encodeEdge({
      id: "s",
      source: "a",
      target: "b",
      tipo: "SEQUENZA",
    });
    const causa = encodeEdge({
      id: "c",
      source: "a",
      target: "b",
      tipo: "CAUSA",
    });
    expect(edge.family).toBe("struttura");
    expect(edge.family).not.toBe("dizionario");
    expect(edge.color).toBe(SUCCESSIONE_ZONA_COLOR);
    expect(edge.color).not.toBe(seq.color);
    expect(edge.color).not.toBe(causa.color);
    expect(edge.color).not.toBe(DIZIONARIO_COLORS.SEQUENZA);
    expect(edge.color).not.toBe(DIZIONARIO_COLORS.CAUSA);
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

  it("scales edge width from confidenza (0.9 is thicker than 0.4)", () => {
    const high = encodeEdge({
      id: "c-hi",
      source: "a",
      target: "b",
      tipo: "CAUSA",
      confidenza: 0.9,
    });
    const low = encodeEdge({
      id: "c-lo",
      source: "a",
      target: "b",
      tipo: "CAUSA",
      confidenza: 0.4,
    });
    const missing = encodeEdge({
      id: "c",
      source: "a",
      target: "b",
      tipo: "CAUSA",
    });
    expect(high.width).toBe(5.5);
    expect(low.width).toBe(3);
    expect(missing.width).toBe(2.5);
    expect(high.width).toBeGreaterThan(low.width);
  });
});
