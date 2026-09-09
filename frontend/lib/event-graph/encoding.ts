/**
 * Visual encoding for the event graph (piano 15.3).
 * Pure functions — no cytoscape import, safe for unit tests.
 */

import type {
  EventGraphEdgeData,
  EventGraphEdgeElement,
  EventGraphNodeData,
  EventGraphNodeElement,
} from "./types";

export const PIANO_COLORS = {
  PRIMO_PIANO: "#1D4ED8",
  SFONDO: "#94A3B8",
  FUORI_LINEA: "#D97706",
} as const;

export const PIANO_FALLBACK = "#334155";

export const MENZIONE_COLOR = "#64748B";
export const QUARANTENA_COLOR = "#B45309";

export const ARGOMENTALE_COLOR = "#94A3B8";
export const COLLEGATO_COLOR = "#E2E8F0";
export const CONFLITTO_BORDER = "#DC2626";

export const DIZIONARIO_COLORS: Record<string, string> = {
  CAUSA: "#E11D48",
  PRECEDE: "#0D9488",
  LIMITE: "#CA8A04",
  CONDIZIONE: "#7C3AED",
  SCOPO: "#2563EB",
  CONCESSIONE: "#DB2777",
  CONTRASTO: "#EA580C",
  SEQUENZA: "#1D4ED8",
  CONTENUTO: "#0F766E",
};

export const STRUTTURA_COLOR = "#0F766E";
/** Node-trait swatch only — catena is not an arc family. */
export const CATENA_COLOR = "#4F46E5";

export const ARGOMENTALI = new Set([
  "SOGG",
  "OGG",
  "OBL",
  "TEMPO",
  "LUOGO",
  "MODO",
]);

export const DIZIONARIO = new Set([
  "CAUSA",
  "PRECEDE",
  "LIMITE",
  "CONDIZIONE",
  "SCOPO",
  "CONCESSIONE",
  "CONTRASTO",
  "SEQUENZA",
  "CONTENUTO",
]);

/** Leftover chain-typed edges in a payload: ignore, never style as catena. */
const CHAIN_TIPI_IGNORATI = new Set(["STESSO_EVENTO", "AGGIORNA", "CONTRADDICE"]);

export type EdgeFamily =
  | "argomentali"
  | "dizionario"
  | "temporale"
  | "placeholder"
  | "struttura"
  | "other";

export type EncodedNode = {
  color: string;
  borderColor: string;
  borderWidth: number;
  borderStyle: "solid" | "dashed";
  shape: "ellipse" | "round-rectangle";
  filled: boolean;
  desaturated: boolean;
  opacity: number;
};

export type EncodedEdge = {
  color: string;
  width: number;
  lineStyle: "solid" | "dashed";
  family: EdgeFamily;
  double: boolean;
  markedArrow: boolean;
  opacity: number;
  borderColor: string | null;
};

function asNodeData(
  node: EventGraphNodeData | EventGraphNodeElement,
): EventGraphNodeData {
  if ("data" in node && node.data && typeof node.data === "object" && "id" in node.data) {
    return node.data;
  }
  return node as EventGraphNodeData;
}

function asEdgeData(
  edge: EventGraphEdgeData | EventGraphEdgeElement,
): EventGraphEdgeData {
  if ("data" in edge && edge.data && typeof edge.data === "object" && "id" in edge.data) {
    return edge.data;
  }
  return edge as EventGraphEdgeData;
}

function hexToRgb(hex: string): [number, number, number] {
  const n = hex.replace("#", "");
  return [
    Number.parseInt(n.slice(0, 2), 16),
    Number.parseInt(n.slice(2, 4), 16),
    Number.parseInt(n.slice(4, 6), 16),
  ];
}

function rgbToHex(r: number, g: number, b: number): string {
  return `#${[r, g, b]
    .map((c) => Math.max(0, Math.min(255, c)).toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase()}`;
}

export function desaturateHex(hex: string, amount = 0.55): string {
  const [r, g, b] = hexToRgb(hex);
  const gray = 0.299 * r + 0.587 * g + 0.114 * b;
  const mix = (c: number) => Math.round(c + (gray - c) * amount);
  return rgbToHex(mix(r), mix(g), mix(b));
}

export function edgeFamily(tipo: string): EdgeFamily {
  const t = (tipo ?? "").toUpperCase();
  if (CHAIN_TIPI_IGNORATI.has(t)) return "other";
  if (t === "PRECEDE") return "temporale";
  if (ARGOMENTALI.has(t)) return "argomentali";
  if (t === "COLLEGATO") return "placeholder";
  if (t === "SATELLITE_DI") return "struttura";
  if (DIZIONARIO.has(t)) return "dizionario";
  return "other";
}

export function encodeNode(
  node: EventGraphNodeData | EventGraphNodeElement,
): EncodedNode {
  const data = asNodeData(node);
  const tipo = String(data.tipo ?? "Evento");
  const piano = String(data.piano ?? "");
  const fattualita = String(data.fattualita ?? "");
  const desaturated = Boolean(fattualita) && fattualita !== "FATTUALE";

  let color: string = PIANO_FALLBACK;
  if (tipo === "Menzione") {
    color = MENZIONE_COLOR;
  } else if (tipo === "Quarantena") {
    color = QUARANTENA_COLOR;
  } else if (piano === "PRIMO_PIANO") {
    color = PIANO_COLORS.PRIMO_PIANO;
  } else if (piano === "SFONDO") {
    color = PIANO_COLORS.SFONDO;
  } else if (piano === "FUORI_LINEA") {
    color = PIANO_COLORS.FUORI_LINEA;
  }

  if (desaturated) {
    color = desaturateHex(color);
  }

  const filled = tipo === "Evento";
  const thin = tipo === "Menzione";
  const dashed = tipo === "Quarantena";

  return {
    color,
    borderColor: color,
    borderWidth: thin ? 1 : 2,
    borderStyle: dashed ? "dashed" : "solid",
    shape: "ellipse",
    filled,
    desaturated,
    opacity: 1,
  };
}

export function encodeEdge(
  edge: EventGraphEdgeData | EventGraphEdgeElement,
): EncodedEdge {
  const data = asEdgeData(edge);
  const tipo = String(data.tipo ?? "").toUpperCase();
  const family = edgeFamily(tipo);
  const base = String(data.base ?? "");
  const segnale = String(data.segnale ?? "");
  const superato = data.superato_da != null && String(data.superato_da) !== "";
  const conflitto = data.conflitto === true;

  let color = ARGOMENTALE_COLOR;
  let width = 2;
  let lineStyle: "solid" | "dashed" = "solid";
  let double = false;
  let markedArrow = false;

  if (family === "argomentali") {
    color = ARGOMENTALE_COLOR;
    width = 1;
  } else if (family === "dizionario") {
    color = DIZIONARIO_COLORS[tipo] ?? ARGOMENTALE_COLOR;
    width = 2.5;
  } else if (family === "temporale") {
    color = DIZIONARIO_COLORS.PRECEDE;
    width = 2.5;
    markedArrow = true;
    lineStyle = base === "riferimento_testuale" ? "dashed" : "solid";
  } else if (family === "placeholder") {
    color = COLLEGATO_COLOR;
    width = 1;
    if (segnale.startsWith("ordine_")) {
      color = COLLEGATO_COLOR;
    }
  } else if (family === "struttura") {
    color = STRUTTURA_COLOR;
    width = 2;
  }

  return {
    color,
    width,
    lineStyle,
    family,
    double,
    markedArrow,
    opacity: superato ? 0.35 : 1,
    borderColor: conflitto ? CONFLITTO_BORDER : null,
  };
}
