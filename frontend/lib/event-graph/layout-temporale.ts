/**
 * Pure ordering and drill-in filtering for the Livello 2 "Temporale" view.
 * The backend owns time: the client sorts on ordinale / chiave_ordine /
 * posizione_doc_min and never parses dates. No cytoscape — unit-testable
 * without a browser.
 */

import type {
  EventGraphElements,
  EventGraphNodeData,
  EventGraphNodeElement,
} from "./types";

const BANDA_DATATO = 0;
const BANDA_NARRATIVO = 1;
const BANDA_IGNOTO = 2;

const ANCORA_TIPI = new Set(["AncoraTemporale", "ClusterTemporale"]);

type AncoraInfo = {
  id: string;
  parent: string | null;
  ordinale: number | null;
  chiaveOrdine: number | null;
  posizioneDocMin: number | null;
  ordineVista: number | null;
};

type VistaKey = readonly [banda: number, valore: number, id: string];

export type TimelineRankEdge = {
  data: {
    id: string;
    source: string;
    target: string;
    tipo: "SUCCESSIONE_ANCORA";
  };
};

function nodeData(
  node: EventGraphNodeElement | EventGraphNodeData | null | undefined,
): EventGraphNodeData | null {
  if (!node || typeof node !== "object") return null;
  if ("data" in node && node.data && typeof node.data === "object" && "id" in node.data) {
    return node.data;
  }
  if ("id" in node) return node as EventGraphNodeData;
  return null;
}

function asInt(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return null;
    const parsed = Number(trimmed);
    if (Number.isFinite(parsed)) return Math.trunc(parsed);
  }
  return null;
}

function asId(value: unknown): string | null {
  if (value == null || value === "") return null;
  if (typeof value === "string" || typeof value === "number") {
    const id = String(value);
    return id || null;
  }
  return null;
}

function campoLettura(value: unknown): [number, number] {
  const n = asInt(value);
  return n == null ? [1, 0] : [0, n];
}

/**
 * Text order inside an ancora box: offset_inizio (global char offset), then
 * posizione_doc (zona ordinal in practice), posizione_chunk, then id.
 */
function chiaveLetturaEvento(
  data: EventGraphNodeData,
): readonly [number, number, number, number, number, number, string] {
  const [offManca, offVal] = campoLettura(data.offset_inizio);
  const [posManca, posVal] = campoLettura(data.posizione_doc);
  const [chunkManca, chunkVal] = campoLettura(data.posizione_chunk);
  return [offManca, offVal, posManca, posVal, chunkManca, chunkVal, String(data.id ?? "")];
}

function confrontaLetturaEvento(a: EventGraphNodeData, b: EventGraphNodeData): number {
  const ka = chiaveLetturaEvento(a);
  const kb = chiaveLetturaEvento(b);
  for (let i = 0; i < 6; i += 1) {
    const da = ka[i] as number;
    const db = kb[i] as number;
    if (da !== db) return da - db;
  }
  return ka[6].localeCompare(kb[6]);
}

function valoreLetturaEvento(data: EventGraphNodeData): number | null {
  return (
    asInt(data.offset_inizio) ?? asInt(data.posizione_doc) ?? asInt(data.posizione_chunk)
  );
}

export function isAncoraTemporaleNode(
  node: EventGraphNodeElement | EventGraphNodeData | null | undefined,
): boolean {
  const data = node ? nodeData(node) : null;
  return data != null && ANCORA_TIPI.has(String(data.tipo ?? ""));
}

function ordinaNodiVista(nodes: EventGraphNodeElement[]): EventGraphNodeElement[] {
  const ancore: EventGraphNodeElement[] = [];
  const eventi: EventGraphNodeElement[] = [];
  for (const node of nodes) {
    if (isAncoraTemporaleNode(node)) ancore.push(node);
    else eventi.push(node);
  }
  eventi.sort((a, b) => confrontaLetturaEvento(a.data, b.data));
  return [...ancore, ...eventi];
}

/** Compact hub label — descrizione stays on the tooltip / inspector. */
export function ancoraDisplayLabel(
  node: EventGraphNodeElement | EventGraphNodeData | null | undefined,
): string {
  const data = node ? nodeData(node) : null;
  if (!data) return "";
  const etichetta = data.etichetta != null ? String(data.etichetta).trim() : "";
  if (etichetta) return etichetta;
  return String(data.label ?? data.id ?? "");
}

function ciclo(padreDi: Map<string, string>): Set<string> | null {
  for (const start of [...padreDi.keys()].sort()) {
    const percorso: string[] = [];
    let corrente: string | undefined = start;
    while (corrente != null) {
      if (percorso.includes(corrente)) {
        return new Set(percorso.slice(percorso.indexOf(corrente)));
      }
      percorso.push(corrente);
      corrente = padreDi.get(corrente);
    }
  }
  return null;
}

function padriValidi(
  padreDi: Map<string, string>,
  ids: Set<string>,
): Map<string, string> {
  const validi = new Map<string, string>();
  for (const [figlio, padre] of padreDi) {
    if (ids.has(padre) && padre !== figlio) validi.set(figlio, padre);
  }
  let trovato = ciclo(validi);
  while (trovato) {
    const taglio = [...trovato].sort()[0];
    if (taglio) validi.delete(taglio);
    trovato = ciclo(validi);
  }
  return validi;
}

function minimiSottoalbero(
  propri: Map<string, number | null>,
  padreDi: Map<string, string>,
): Map<string, number | null> {
  const fuori = new Map(propri);
  for (const cid of [...propri.keys()].sort()) {
    const valore = propri.get(cid);
    if (valore == null) continue;
    const visti = new Set([cid]);
    let corrente = padreDi.get(cid);
    while (corrente != null && !visti.has(corrente)) {
      visti.add(corrente);
      const attuale = fuori.get(corrente);
      if (attuale == null || valore < attuale) fuori.set(corrente, valore);
      corrente = padreDi.get(corrente);
    }
  }
  return fuori;
}

function chiaveVista(
  cid: string,
  chiaveOrdine: number | null,
  posizioneDocMin: number | null,
): VistaKey {
  if (chiaveOrdine != null) return [BANDA_DATATO, chiaveOrdine, cid];
  if (posizioneDocMin != null) return [BANDA_NARRATIVO, posizioneDocMin, cid];
  return [BANDA_IGNOTO, 0, cid];
}

function confrontaChiavi(a: VistaKey, b: VistaKey): number {
  if (a[0] !== b[0]) return a[0] - b[0];
  if (a[1] !== b[1]) return a[1] - b[1];
  return a[2].localeCompare(b[2]);
}

function confrontaFratelli(
  a: string,
  b: string,
  ordinali: Map<string, number | null>,
  chiavi: Map<string, VistaKey>,
): number {
  const oa = ordinali.get(a) ?? null;
  const ob = ordinali.get(b) ?? null;
  if (oa != null && ob != null && oa !== ob) return oa - ob;
  return confrontaChiavi(
    chiavi.get(a) ?? chiaveVista(a, null, null),
    chiavi.get(b) ?? chiaveVista(b, null, null),
  );
}

function preorder(
  ids: string[],
  padreDi: Map<string, string>,
  chiavi: Map<string, VistaKey>,
  ordinali: Map<string, number | null>,
): string[] {
  const figli = new Map<string, string[]>();
  const radici: string[] = [];
  for (const cid of ids) {
    const padre = padreDi.get(cid);
    if (padre == null) radici.push(cid);
    else {
      const list = figli.get(padre) ?? [];
      list.push(cid);
      figli.set(padre, list);
    }
  }
  const byKey = (a: string, b: string) => confrontaFratelli(a, b, ordinali, chiavi);
  const ordine: string[] = [];
  const pila = [...radici].sort(byKey).reverse();
  while (pila.length) {
    const cid = pila.pop() as string;
    ordine.push(cid);
    const next = [...(figli.get(cid) ?? [])].sort(byKey).reverse();
    pila.push(...next);
  }
  return ordine;
}

function collectAncore(nodes: EventGraphNodeElement[]): {
  ancore: AncoraInfo[];
  eventi: EventGraphNodeData[];
} {
  const ancore: AncoraInfo[] = [];
  const eventi: EventGraphNodeData[] = [];
  if (!Array.isArray(nodes)) return { ancore, eventi };
  for (const node of nodes) {
    const data = nodeData(node);
    if (!data?.id) continue;
    const tipo = String(data.tipo ?? "");
    if (ANCORA_TIPI.has(tipo)) {
      ancore.push({
        id: String(data.id),
        parent: asId(data.parent),
        ordinale: asInt(data.ordinale),
        chiaveOrdine: asInt(data.chiave_ordine),
        posizioneDocMin: asInt(data.posizione_doc_min),
        ordineVista: asInt(data.ordine_vista),
      });
    } else if (tipo === "Evento") {
      eventi.push(data);
    }
  }
  return { ancore, eventi };
}

function resolveAncoraOrder(nodes: EventGraphNodeElement[]): {
  ordered: string[];
  padreDi: Map<string, string>;
} {
  const { ancore, eventi } = collectAncore(nodes);
  if (ancore.length === 0) return { ordered: [], padreDi: new Map() };

  const ids = new Set(ancore.map((c) => c.id));
  const padreGrezzo = new Map<string, string>();
  for (const ancora of ancore) {
    if (ancora.parent) padreGrezzo.set(ancora.id, ancora.parent);
  }
  const padreDi = padriValidi(padreGrezzo, ids);

  const chiaviProprie = new Map<string, number | null>();
  const posizioniProprie = new Map<string, number | null>();
  const ordinali = new Map<string, number | null>();
  for (const ancora of ancore) {
    chiaviProprie.set(ancora.id, ancora.chiaveOrdine);
    posizioniProprie.set(ancora.id, ancora.posizioneDocMin);
    ordinali.set(ancora.id, ancora.ordinale);
  }
  for (const evento of eventi) {
    const parent = asId(evento.parent);
    // Same reading key as inside the box: offset_inizio first. posizione_doc
    // is the zona ordinal in practice, used only when offset is missing.
    const lettura = valoreLetturaEvento(evento);
    if (parent == null || lettura == null || !ids.has(parent)) continue;
    const attuale = posizioniProprie.get(parent);
    if (attuale == null || lettura < attuale) {
      posizioniProprie.set(parent, lettura);
    }
  }

  const chiavi = minimiSottoalbero(chiaviProprie, padreDi);
  const posizioni = minimiSottoalbero(posizioniProprie, padreDi);
  const chiaviVista = new Map<string, VistaKey>();
  for (const ancora of ancore) {
    chiaviVista.set(
      ancora.id,
      chiaveVista(
        ancora.id,
        chiavi.get(ancora.id) ?? null,
        posizioni.get(ancora.id) ?? null,
      ),
    );
  }

  const tuttiOrdineVista = ancore.every((c) => c.ordineVista != null);
  const ordered = tuttiOrdineVista
    ? [...ancore]
        .sort((a, b) => {
          const diff = (a.ordineVista as number) - (b.ordineVista as number);
          if (diff !== 0) return diff;
          return a.id.localeCompare(b.id);
        })
        .map((c) => c.id)
    : preorder(
        ancore.map((c) => c.id),
        padreDi,
        chiaviVista,
        ordinali,
      );

  return { ordered, padreDi };
}

/**
 * Display order of AncoraTemporale ids: siblings prefer ordinale when
 * present, else dated (chiave_ordine), then narrative (posizione_doc_min),
 * then unknown. Preorder so every parent precedes its children. Id is only
 * a last-resort tie-break inside a band.
 */
export function sortClusterIds(nodes: EventGraphNodeElement[]): string[] {
  try {
    return resolveAncoraOrder(nodes).ordered;
  } catch {
    return [];
  }
}

/**
 * SUCCESSIONE_ANCORA between consecutive **siblings** (same parent, or both
 * roots). Never parent→child, never uncle→nephew, never fake PRECEDE.
 * Used only when the payload has no SUCCESSIONE_ANCORA yet (layout fallback).
 */
export function timelineRankEdges(nodes: EventGraphNodeElement[]): TimelineRankEdge[] {
  try {
    const { ordered, padreDi } = resolveAncoraOrder(nodes);
    const gruppi = new Map<string, string[]>();
    for (const cid of ordered) {
      const padre = padreDi.get(cid) ?? "";
      const list = gruppi.get(padre) ?? [];
      list.push(cid);
      gruppi.set(padre, list);
    }
    const edges: TimelineRankEdge[] = [];
    for (const fratelli of gruppi.values()) {
      for (let i = 0; i < fratelli.length - 1; i += 1) {
        const source = fratelli[i];
        const target = fratelli[i + 1];
        edges.push({
          data: {
            id: `__timeline_rank__${source}__${target}`,
            source,
            target,
            tipo: "SUCCESSIONE_ANCORA",
          },
        });
      }
    }
    return edges;
  } catch {
    return [];
  }
}

export function isTimelineRankEdgeId(id: string): boolean {
  return typeof id === "string" && id.startsWith("__timeline_rank__");
}

function stripMissingParent(
  node: EventGraphNodeElement,
  keep: Set<string>,
): EventGraphNodeElement {
  const data = { ...node.data };
  const parent = asId(data.parent);
  if (parent == null || !keep.has(parent)) {
    delete data.parent;
  }
  return { ...node, data };
}

function resolveFocusPath(
  path: string[] | null | undefined,
  ancoraIds: Set<string>,
  padreDi: Map<string, string>,
): string[] {
  if (!Array.isArray(path) || path.length === 0) return [];
  const resolved: string[] = [];
  for (const raw of path) {
    const id = asId(raw);
    if (id == null || !ancoraIds.has(id)) break;
    if (resolved.length > 0) {
      const parent = padreDi.get(id) ?? null;
      if (parent !== resolved[resolved.length - 1]) break;
    }
    resolved.push(id);
  }
  return resolved;
}

/**
 * Overview = root AncoraTemporale hubs + SUCCESSIONE_ANCORA among them.
 * Focused on path [..., A] = A + child ancore + events with parent === A,
 * plus edges among those nodes. Nested boxes stay hidden until drill-in.
 */
export function filterTemporaleElements(
  elements: EventGraphElements | null | undefined,
  ancoraPath: string[] | null | undefined,
): EventGraphElements {
  const nodes = elements?.nodes ?? [];
  const edges = elements?.edges ?? [];
  const ancore = nodes.filter((node) => isAncoraTemporaleNode(node));
  const ancoraIds = new Set<string>();
  const padreGrezzo = new Map<string, string>();
  for (const node of ancore) {
    const data = nodeData(node);
    if (!data?.id) continue;
    ancoraIds.add(data.id);
    const parent = asId(data.parent);
    if (parent) padreGrezzo.set(data.id, parent);
  }
  const padreDi = padriValidi(padreGrezzo, ancoraIds);
  const rootIds = new Set(
    [...ancoraIds].filter((id) => {
      const parent = padreDi.get(id);
      return parent == null || !ancoraIds.has(parent);
    }),
  );

  const focusPath = resolveFocusPath(ancoraPath, ancoraIds, padreDi);
  const focusedId = focusPath.length > 0 ? focusPath[focusPath.length - 1] : null;

  if (focusedId == null) {
    const keep = rootIds;
    return {
      nodes: ordinaNodiVista(
        ancore
          .filter((node) => {
            const id = nodeData(node)?.id;
            return Boolean(id && keep.has(id));
          })
          .map((node) => stripMissingParent(node, keep)),
      ),
      edges: edges.filter((edge) => {
        const tipo = String(edge.data?.tipo ?? "");
        return (
          tipo === "SUCCESSIONE_ANCORA" &&
          keep.has(edge.data.source) &&
          keep.has(edge.data.target)
        );
      }),
    };
  }

  const keep = new Set<string>([focusedId]);
  for (const node of nodes) {
    const data = nodeData(node);
    if (!data?.id) continue;
    if (data.parent !== focusedId) continue;
    const tipo = String(data.tipo ?? "");
    if (ANCORA_TIPI.has(tipo) || tipo === "Evento") keep.add(data.id);
  }
  return {
    nodes: ordinaNodiVista(
      nodes
        .filter((node) => {
          const data = nodeData(node);
          return data != null && keep.has(data.id);
        })
        .map((node) => stripMissingParent(node, keep)),
    ),
      edges: edges.filter((edge) => {
        const tipo = String(edge.data?.tipo ?? "");
        if (tipo !== "SUCCESSIONE_ANCORA") {
          return false;
        }
        return keep.has(edge.data.source) && keep.has(edge.data.target);
      }),
  };
}
