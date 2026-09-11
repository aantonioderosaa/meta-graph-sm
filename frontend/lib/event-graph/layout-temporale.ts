/**
 * Pure cluster ordering for the Livello 2 "Temporale" timeline (MT8).
 * The backend owns time: the client sorts on chiave_ordine / posizione_doc_min
 * and never parses dates. No cytoscape — unit-testable without a browser.
 */

import type { EventGraphNodeData, EventGraphNodeElement } from "./types";

const BANDA_DATATO = 0;
const BANDA_NARRATIVO = 1;
const BANDA_IGNOTO = 2;

type ClusterInfo = {
  id: string;
  parent: string | null;
  chiaveOrdine: number | null;
  posizioneDocMin: number | null;
  ordineVista: number | null;
};

type VistaKey = readonly [banda: number, valore: number, id: string];

export type TimelineRankEdge = {
  data: { id: string; source: string; target: string; tipo: "PRECEDE" };
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

function preorder(
  ids: string[],
  padreDi: Map<string, string>,
  chiavi: Map<string, VistaKey>,
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
  const byKey = (a: string, b: string) =>
    confrontaChiavi(
      chiavi.get(a) ?? chiaveVista(a, null, null),
      chiavi.get(b) ?? chiaveVista(b, null, null),
    );
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

function collectClusters(nodes: EventGraphNodeElement[]): {
  clusters: ClusterInfo[];
  eventi: EventGraphNodeData[];
} {
  const clusters: ClusterInfo[] = [];
  const eventi: EventGraphNodeData[] = [];
  if (!Array.isArray(nodes)) return { clusters, eventi };
  for (const node of nodes) {
    const data = nodeData(node);
    if (!data?.id) continue;
    const tipo = String(data.tipo ?? "");
    if (tipo === "ClusterTemporale") {
      clusters.push({
        id: String(data.id),
        parent: asId(data.parent),
        chiaveOrdine: asInt(data.chiave_ordine),
        posizioneDocMin: asInt(data.posizione_doc_min),
        ordineVista: asInt(data.ordine_vista),
      });
    } else if (tipo === "Evento") {
      eventi.push(data);
    }
  }
  return { clusters, eventi };
}

function resolveClusterOrder(nodes: EventGraphNodeElement[]): {
  ordered: string[];
  padreDi: Map<string, string>;
} {
  const { clusters, eventi } = collectClusters(nodes);
  if (clusters.length === 0) return { ordered: [], padreDi: new Map() };

  const ids = new Set(clusters.map((c) => c.id));
  const padreGrezzo = new Map<string, string>();
  for (const cluster of clusters) {
    if (cluster.parent) padreGrezzo.set(cluster.id, cluster.parent);
  }
  const padreDi = padriValidi(padreGrezzo, ids);

  const chiaviProprie = new Map<string, number | null>();
  const posizioniProprie = new Map<string, number | null>();
  for (const cluster of clusters) {
    chiaviProprie.set(cluster.id, cluster.chiaveOrdine);
    posizioniProprie.set(cluster.id, cluster.posizioneDocMin);
  }
  for (const evento of eventi) {
    const parent = asId(evento.parent);
    const posizione = asInt(evento.posizione_doc);
    if (parent == null || posizione == null || !ids.has(parent)) continue;
    const attuale = posizioniProprie.get(parent);
    if (attuale == null || posizione < attuale) {
      posizioniProprie.set(parent, posizione);
    }
  }

  const chiavi = minimiSottoalbero(chiaviProprie, padreDi);
  const posizioni = minimiSottoalbero(posizioniProprie, padreDi);
  const chiaviVista = new Map<string, VistaKey>();
  for (const cluster of clusters) {
    chiaviVista.set(
      cluster.id,
      chiaveVista(
        cluster.id,
        chiavi.get(cluster.id) ?? null,
        posizioni.get(cluster.id) ?? null,
      ),
    );
  }

  const tuttiOrdineVista = clusters.every((c) => c.ordineVista != null);
  const ordered = tuttiOrdineVista
    ? [...clusters]
        .sort((a, b) => {
          const diff = (a.ordineVista as number) - (b.ordineVista as number);
          if (diff !== 0) return diff;
          return a.id.localeCompare(b.id);
        })
        .map((c) => c.id)
    : preorder(
        clusters.map((c) => c.id),
        padreDi,
        chiaviVista,
      );

  return { ordered, padreDi };
}

/**
 * Display order of ClusterTemporale ids: dated (chiave_ordine), then
 * narrative (posizione_doc_min), then unknown. Preorder so every parent
 * precedes its children. Id is only a last-resort tie-break inside a band.
 */
export function sortClusterIds(nodes: EventGraphNodeElement[]): string[] {
  try {
    return resolveClusterOrder(nodes).ordered;
  } catch {
    return [];
  }
}

/**
 * Dummy PRECEDE edges between consecutive **siblings** (same parent, or both
 * roots). Never parent→child, never uncle→nephew. Cytoscape copy only.
 */
export function timelineRankEdges(nodes: EventGraphNodeElement[]): TimelineRankEdge[] {
  try {
    const { ordered, padreDi } = resolveClusterOrder(nodes);
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
            tipo: "PRECEDE",
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
