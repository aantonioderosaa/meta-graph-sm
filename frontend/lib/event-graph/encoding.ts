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
export const ZONA_COLOR = "#57534E";
/** Temporal ancora hub — filled round-rectangle, distinct from Zona. */
export const CLUSTER_TEMPORALE_COLOR = "#0E7490";

export const ARGOMENTALE_COLOR = "#94A3B8";
export const COLLEGATO_COLOR = "#E2E8F0";
export const CONFLITTO_BORDER = "#DC2626";

export const DIZIONARIO_COLORS: Record<string, string> = {
  CAUSA: "#E11D48",
  LIMITE: "#CA8A04",
  CONDIZIONE: "#7C3AED",
  SCOPO: "#2563EB",
  CONCESSIONE: "#DB2777",
  CONTRASTO: "#EA580C",
  SEQUENZA: "#1D4ED8",
  CONTENUTO: "#0F766E",
};

export const STRUTTURA_COLOR = "#0F766E";
/** Trunk edge between Zona hubs — struttura family, not SEQUENZA/CAUSA. */
export const SUCCESSIONE_ZONA_COLOR = "#A16207";
/** Sibling order between AncoraTemporale hubs — same struttura family as zone. */
export const SUCCESSIONE_ANCORA_COLOR = SUCCESSIONE_ZONA_COLOR;
/** Node-trait swatch only — catena is not an arc family. */
export const CATENA_COLOR = "#4F46E5";

/** Keys are EntitaKernelCategoria values (event-graph local vocabulary, mirrors
 * frontend/lib/graph-encoding.ts's E1-E8 palette for visual consistency —
 * declared locally, no cross-import into the dormant Metagraph module). */
export const KERNEL_CATEGORY_COLORS: Record<string, string> = {
  Agente: "#0369A1",
  OggettoFisico: "#B45309",
  Luogo: "#15803D",
  Evento: "#BE123C",
  EntitaTemporale: "#0F766E",
  EntitaInformativa: "#7C3AED",
  CostruttoSociale: "#C2410C",
  EntitaAstratta: "#334155",
  Temporale: "#0EA5E9",
};

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

/** Neo4j / cytoscape may surface bool flags as `"true"` strings. */
function isTruthyFlag(value: unknown): boolean {
  if (value === true || value === 1) return true;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    return normalized === "true" || normalized === "1";
  }
  return false;
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
  if (ARGOMENTALI.has(t)) return "argomentali";
  if (t === "COLLEGATO") return "placeholder";
  if (
    t === "SATELLITE_DI" ||
    t === "SUCCESSIONE_ZONA" ||
    t === "SUCCESSIONE_ANCORA"
  ) {
    return "struttura";
  }
  if (DIZIONARIO.has(t)) return "dizionario";
  return "other";
}

export function encodeNode(
  node: EventGraphNodeData | EventGraphNodeElement,
): EncodedNode {
  const data = asNodeData(node);
  const tipo = String(data.tipo ?? "Fatto");
  const piano = String(data.piano ?? "");
  const fattualita = String(data.fattualita ?? "");
  const desaturated = Boolean(fattualita) && fattualita !== "FATTUALE";

  let color: string = PIANO_FALLBACK;
  const isAncora = tipo === "AncoraTemporale" || tipo === "ClusterTemporale";
  // Vista "entita" only: member nodes (Menzione/Evento) carry a `categoria`
  // field there and only there — outside that vista this is always empty,
  // so this branch never fires for the "Tutto" gray Menzione styling below.
  const categoriaKernel = data.categoria ? String(data.categoria) : "";
  if (categoriaKernel && (tipo === "Menzione" || tipo === "Fatto")) {
    color = KERNEL_CATEGORY_COLORS[categoriaKernel] ?? MENZIONE_COLOR;
  } else if (tipo === "Zona") {
    color = ZONA_COLOR;
  } else if (isAncora) {
    color = CLUSTER_TEMPORALE_COLOR;
  } else if (tipo === "Menzione") {
    color = MENZIONE_COLOR;
  } else if (tipo === "Quarantena") {
    color = QUARANTENA_COLOR;
  } else if (piano === "PRIMO_PIANO") {
    color = PIANO_COLORS.PRIMO_PIANO;
  } else if (piano === "SFONDO") {
    color = PIANO_COLORS.SFONDO;
  } else if (piano === "FUORI_LINEA") {
    color = PIANO_COLORS.FUORI_LINEA;
  } else if (tipo === "KernelCategoria") {
    color = KERNEL_CATEGORY_COLORS[categoriaKernel] ?? PIANO_FALLBACK;
  }

  if (desaturated) {
    color = desaturateHex(color);
  }

  const isKernelCategoria = tipo === "KernelCategoria";
  const filled = tipo === "Fatto" || tipo === "Zona" || isAncora || isKernelCategoria;
  const thin = tipo === "Menzione";
  const dashed =
    tipo === "Quarantena" || (isAncora && isTruthyFlag(data.stimato));
  const hub = tipo === "Zona" || isAncora || isKernelCategoria;

  return {
    color,
    borderColor: color,
    borderWidth: thin ? 1 : 2,
    borderStyle: dashed ? "dashed" : "solid",
    shape: hub ? "round-rectangle" : "ellipse",
    filled,
    desaturated,
    opacity: 1,
  };
}

/** Uniform stroke for every arc. `confidenza` stays on the payload (tooltip / inspector). */
export const EDGE_WIDTH = 2.5;

export function encodeEdge(
  edge: EventGraphEdgeData | EventGraphEdgeElement,
): EncodedEdge {
  const data = asEdgeData(edge);
  const tipo = String(data.tipo ?? "").toUpperCase();
  const family = edgeFamily(tipo);
  const segnale = String(data.segnale ?? "");
  const superato = data.superato_da != null && String(data.superato_da) !== "";
  const conflitto = data.conflitto === true;

  let color = ARGOMENTALE_COLOR;
  let lineStyle: "solid" | "dashed" = "solid";
  let double = false;
  let markedArrow = false;

  if (family === "argomentali") {
    color = ARGOMENTALE_COLOR;
  } else if (family === "dizionario") {
    color = DIZIONARIO_COLORS[tipo] ?? ARGOMENTALE_COLOR;
    markedArrow = true;
  } else if (family === "placeholder") {
    color = COLLEGATO_COLOR;
    if (segnale.startsWith("ordine_")) {
      color = COLLEGATO_COLOR;
    }
  } else if (family === "struttura") {
    color =
      tipo === "SUCCESSIONE_ZONA" || tipo === "SUCCESSIONE_ANCORA"
        ? SUCCESSIONE_ZONA_COLOR
        : STRUTTURA_COLOR;
  }

  return {
    color,
    width: EDGE_WIDTH,
    lineStyle,
    family,
    double,
    markedArrow,
    opacity: superato ? 0.35 : 1,
    borderColor: conflitto ? CONFLITTO_BORDER : null,
  };
}
