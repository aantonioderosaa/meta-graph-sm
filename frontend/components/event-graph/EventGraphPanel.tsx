"use client";

import { useEffect, useRef, useState } from "react";

import { encodeEdge, encodeNode } from "@/lib/event-graph/encoding";
import {
  HIGHLIGHT_COLORS,
  type HighlightKind,
} from "@/lib/event-graph/highlight";
import { positionsEntita } from "@/lib/event-graph/layout-entita";
import {
  positionsOrdine,
  zonaDisplayLabel,
} from "@/lib/event-graph/layout-ordine";
import { coseRelazioniLayoutOptions } from "@/lib/event-graph/layout-relazioni";
import { ladderEdgeLabel } from "@/lib/event-graph/layout-zigzag";
import {
  isTimelineRankEdgeId,
  timelineRankEdges,
} from "@/lib/event-graph/layout-temporale";
import { legendOpacity, type LegendFilter } from "@/lib/event-graph/legend";
import {
  LIVE_GRAPH_BATCH_SIZE,
  chunkArray,
  diffIds,
  gridPosition,
  shouldUseHeavyLayout,
  sortNodesChildrenFirst,
  sortNodesParentsFirst,
} from "@/lib/event-graph/live-graph";
import type {
  ElementSelection,
  EventGraphEdgeData,
  EventGraphEdgeElement,
  EventGraphElements,
  EventGraphNodeData,
  EventGraphNodeElement,
} from "@/lib/event-graph/types";
import { cn } from "@/lib/utils";

const EMPTY_NODE_ELEMENTS: EventGraphNodeElement[] = [];
const EMPTY_EDGE_ELEMENTS: EventGraphEdgeElement[] = [];

let dagreRegistered = false;

export type EventGraphLayout = "dagre" | "ordine" | "temporale" | "cose" | "entita";

type EventGraphPanelProps = {
  elements?: EventGraphElements | null;
  highlights?: Record<string, HighlightKind>;
  legendFilter?: LegendFilter;
  layout?: EventGraphLayout;
  live?: boolean;
  onSelect?: (selection: ElementSelection | null) => void;
  className?: string;
};

type CyEle = {
  id: () => string;
  data: ((key?: string, value?: unknown) => unknown) & ((value: object) => unknown);
  style: (value: Record<string, unknown>) => void;
  position: (value?: { x: number; y: number }) => { x: number; y: number };
  remove: () => void;
  isParent?: () => boolean;
};

type CyLike = {
  destroy: () => void;
  fit: (eles?: unknown, padding?: number) => void;
  on: (event: string, ...rest: unknown[]) => void;
  batch: (fn: () => void) => void;
  add: (eles: unknown) => { forEach: (fn: (ele: CyEle) => void) => void };
  getElementById: (id: string) => CyEle & { empty?: () => boolean; length?: number };
  nodes: () => { forEach: (fn: (ele: CyEle) => void) => void };
  edges: () => { forEach: (fn: (ele: CyEle) => void) => void };
  layout: (opts: Record<string, unknown>) => { run: () => void };
};

function nodeSignature(data: EventGraphNodeData): string {
  return [
    data.id,
    data.label,
    data.tipo,
    data.piano,
    data.parent,
    data.etichetta,
    data.riassunto,
    data.fattualita,
    data.stimato,
  ].join("\t");
}

function edgeSignature(data: EventGraphEdgeData): string {
  return [
    data.id,
    data.source,
    data.target,
    data.tipo,
    data.segnale,
    data.confidenza,
    data.riassunto_transizione,
  ].join("\t");
}

function prepareNodeData(
  data: EventGraphNodeData,
  layout: EventGraphLayout,
  displayedIds: Set<string>,
): Record<string, unknown> {
  const next: Record<string, unknown> = { ...data };
  const useOrdine = layout === "ordine";
  const useCose = layout === "cose";
  const useTemporale = layout === "temporale";
  const useEntita = layout === "entita";
  if (useOrdine && next.parent) {
    next.zona_id = next.parent;
  }
  if (
    useOrdine ||
    useCose ||
    useEntita ||
    next.parent == null ||
    next.parent === "" ||
    (useTemporale && !displayedIds.has(String(next.parent)))
  ) {
    delete next.parent;
  }
  return next;
}

function cyHas(cy: CyLike, id: string): boolean {
  const ele = cy.getElementById(id);
  if (typeof ele.empty === "function") return !ele.empty();
  return (ele.length ?? 1) > 0 && typeof ele.id === "function" && ele.id() === id;
}

export function EventGraphPanel({
  elements,
  highlights,
  legendFilter = null,
  layout = "dagre",
  live = false,
  onSelect,
  className,
}: EventGraphPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<CyLike | null>(null);
  const queueRef = useRef<Array<() => void>>([]);
  const pumpingRef = useRef(false);
  const fittedRef = useRef(false);
  const nodeSigRef = useRef(new Map<string, string>());
  const edgeSigRef = useRef(new Map<string, string>());
  const onSelectRef = useRef(onSelect);
  const highlightsRef = useRef(highlights);
  const legendFilterRef = useRef(legendFilter);
  const liveRef = useRef(live);
  const layoutRef = useRef(layout);
  const [cyEpoch, setCyEpoch] = useState(0);

  onSelectRef.current = onSelect;
  highlightsRef.current = highlights;
  legendFilterRef.current = legendFilter;
  liveRef.current = live;
  layoutRef.current = layout;

  const nodes = elements?.nodes ?? EMPTY_NODE_ELEMENTS;
  const edges = elements?.edges ?? EMPTY_EDGE_ELEMENTS;
  const isEmpty = nodes.length === 0 && edges.length === 0;

  function styleNode(ele: CyEle, useOrdine: boolean) {
    const nodeData = {
      id: String(ele.id()),
      label: String(ele.data("label") ?? ele.id()),
      tipo: String(ele.data("tipo") ?? "Fatto"),
      piano: ele.data("piano"),
      fattualita: ele.data("fattualita"),
      tempo: ele.data("tempo"),
      polarita: ele.data("polarita"),
      modalizzato: ele.data("modalizzato"),
      iterativita: ele.data("iterativita"),
      fonte: ele.data("fonte"),
      parent: ele.data("parent"),
      ordinale: ele.data("ordinale"),
      riassunto: ele.data("riassunto"),
      evento_centrale: ele.data("evento_centrale"),
      etichetta: ele.data("etichetta"),
      tipo_cluster: ele.data("tipo_cluster"),
      descrizione: ele.data("descrizione"),
      stimato: ele.data("stimato"),
      occorrenze: ele.data("occorrenze"),
      riassunti: ele.data("riassunti"),
      categoria: ele.data("categoria"),
      count: ele.data("count"),
    };
    const style = encodeNode(nodeData as EventGraphNodeData);
    const highlight = highlightsRef.current?.[String(ele.id())];
    const isOrdineZona = useOrdine && nodeData.tipo === "Zona";
    const isAncora =
      nodeData.tipo === "AncoraTemporale" ||
      nodeData.tipo === "ClusterTemporale";
    const isZonaHub = nodeData.tipo === "Zona" && !useOrdine;
    const isCompoundParent =
      typeof ele.isParent === "function" && Boolean(ele.isParent());
    const washHub = isZonaHub || (isAncora && isCompoundParent);
    const isHub = isAncora || isZonaHub;
    const etichettaRaw = ele.data("etichetta");
    const descrizioneRaw = ele.data("descrizione");
    const etichetta =
      etichettaRaw != null && String(etichettaRaw) !== ""
        ? String(etichettaRaw)
        : "";
    const descrizione =
      descrizioneRaw != null && String(descrizioneRaw) !== ""
        ? String(descrizioneRaw)
        : "";
    const lemma = String(ele.data("label") ?? ele.id());
    const boxLabel = isOrdineZona
      ? zonaDisplayLabel(nodeData as EventGraphNodeData)
      : etichetta || lemma;
    const riassuntoRaw = ele.data("riassunto");
    const riassunto =
      riassuntoRaw != null && String(riassuntoRaw) !== ""
        ? String(riassuntoRaw)
        : "";
    const occorrenzeRaw = ele.data("occorrenze");
    const occorrenze =
      occorrenzeRaw != null && String(occorrenzeRaw) !== ""
        ? String(occorrenzeRaw)
        : "";
    const riassuntiRaw = ele.data("riassunti");
    let riassunti: string[] = [];
    if (Array.isArray(riassuntiRaw)) {
      riassunti = riassuntiRaw.map((bit) => String(bit).trim()).filter(Boolean);
    } else if (typeof riassuntiRaw === "string" && riassuntiRaw.trim()) {
      try {
        const parsed = JSON.parse(riassuntiRaw) as unknown;
        if (Array.isArray(parsed)) {
          riassunti = parsed.map((bit) => String(bit).trim()).filter(Boolean);
        } else if (typeof parsed === "string" && parsed.trim()) {
          riassunti = [parsed.trim()];
        } else {
          riassunti = [riassuntiRaw.trim()];
        }
      } catch {
        riassunti = [riassuntiRaw.trim()];
      }
    }
    const contesto = riassunti.length ? riassunti.join(" · ") : descrizione;
    if (isOrdineZona && (riassunto || nodeData.label)) {
      ele.data("title", riassunto || String(nodeData.label));
    } else if (contesto || occorrenze) {
      const bits = [
        etichetta || (nodeData.tipo === "Menzione" ? lemma : ""),
        occorrenze ? `${occorrenze} occorrenze` : "",
        contesto,
      ].filter((bit) => bit);
      ele.data("title", bits.join(" · "));
    } else if (etichetta) {
      ele.data("title", etichetta);
    }
    ele.style({
      label: boxLabel,
      "background-color": style.color,
      "border-color": style.borderColor,
      "border-width": style.borderWidth,
      "border-style": style.borderStyle,
      shape: style.shape,
      "background-opacity": washHub ? 0.18 : style.filled ? 1 : 0.15,
      ...(isHub ? { padding: 16 } : {}),
      opacity: legendOpacity(
        style.opacity,
        { data: nodeData as EventGraphNodeData },
        legendFilterRef.current,
      ),
    });
    if (highlight) {
      ele.style({
        "overlay-padding": 8,
        "overlay-color": HIGHLIGHT_COLORS[highlight],
        "overlay-opacity": 0.55,
        "border-width": Math.max(style.borderWidth, 2) + 3,
      });
    } else {
      ele.style({
        "overlay-opacity": 0,
      });
    }
  }

  function styleEdge(ele: CyEle, useNamedEdges: boolean) {
    if (isTimelineRankEdgeId(String(ele.id()))) {
      ele.style({ display: "none", events: "no" });
      return;
    }
    const edgeData = {
      id: String(ele.id()),
      source: String(ele.data("source") ?? ""),
      target: String(ele.data("target") ?? ""),
      tipo: String(ele.data("tipo") ?? ""),
      base: ele.data("base"),
      segnale: ele.data("segnale"),
      superato_da: ele.data("superato_da"),
      conflitto: ele.data("conflitto"),
      riassunto_transizione: ele.data("riassunto_transizione"),
      spiegazione: ele.data("spiegazione"),
      confidenza: ele.data("confidenza"),
    };
    const style = encodeEdge(edgeData as EventGraphEdgeData);
    const riassunto = ele.data("riassunto_transizione");
    const spiegazione = ele.data("spiegazione");
    const confidenza = ele.data("confidenza");
    const titleBits = [
      riassunto,
      spiegazione,
      confidenza != null && confidenza !== ""
        ? `confidenza ${confidenza}`
        : null,
    ].filter((bit) => bit != null && String(bit) !== "");
    if (titleBits.length > 0) {
      ele.data("title", titleBits.map(String).join(" · "));
    }
    const edgeLabel = useNamedEdges ? ladderEdgeLabel(edgeData.tipo) : "";
    ele.style({
      "line-color": style.color,
      "target-arrow-color": style.color,
      "target-arrow-shape": style.markedArrow ? "triangle" : "none",
      width: style.double ? Math.max(style.width, 5) : style.width,
      "line-style": style.lineStyle,
      "arrow-scale": style.markedArrow ? 1.6 : 1,
      opacity: legendOpacity(
        style.opacity,
        { data: edgeData as EventGraphEdgeData },
        legendFilterRef.current,
      ),
      "line-outline-width": style.borderColor ? 2 : 0,
      "line-outline-color": style.borderColor ?? "transparent",
      ...(useNamedEdges
        ? {
            label: edgeLabel,
            color: style.color,
            "font-size": 8,
            "font-weight": 600,
            "text-rotation": "autorotate",
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.92,
            "text-background-padding": 3,
          }
        : { label: "" }),
    });
  }

  function pump() {
    if (pumpingRef.current) return;
    pumpingRef.current = true;
    const step = () => {
      const job = queueRef.current.shift();
      if (!job) {
        pumpingRef.current = false;
        return;
      }
      try {
        job();
      } catch {
        /* keep draining so a bad element does not stall the live view */
      }
      if (queueRef.current.length > 0) {
        requestAnimationFrame(step);
      } else {
        pumpingRef.current = false;
      }
    };
    requestAnimationFrame(step);
  }

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let cancelled = false;
    let cy: CyLike | null = null;

    async function mount() {
      const [{ default: cytoscape }, { default: dagre }] = await Promise.all([
        import("cytoscape"),
        import("cytoscape-dagre"),
      ]);
      if (!dagreRegistered) {
        cytoscape.use(dagre);
        dagreRegistered = true;
      }
      if (cancelled || !containerRef.current) return;

      const useOrdine = layout === "ordine";
      const useTemporale = layout === "temporale";
      const useCose = layout === "cose";
      const useNamedEdges = !useOrdine && !useTemporale;

      const instance = cytoscape({
        container: containerRef.current,
        elements: [],
        layout: { name: "preset", fit: false },
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              "text-valign": "center",
              "text-halign": "center",
              "font-size": 10,
              "text-wrap": "ellipsis",
              "text-max-width": "80px",
              color: "#0f172a",
            },
          },
          {
            selector: useOrdine
              ? 'node:parent, node[tipo = "AncoraTemporale"], node[tipo = "ClusterTemporale"], node[tipo = "KernelCategoria"]'
              : 'node:parent, node[tipo = "Zona"], node[tipo = "AncoraTemporale"], node[tipo = "ClusterTemporale"], node[tipo = "KernelCategoria"]',
            style: {
              shape: "round-rectangle",
              "background-opacity": 0.18,
              padding: 16,
              "font-weight": 600,
              "text-max-width": "120px",
              "text-valign": "center",
              "text-halign": "center",
            },
          },
          ...(useOrdine
            ? [
                {
                  selector: 'node[tipo = "Zona"]',
                  style: {
                    shape: "round-rectangle",
                    "background-opacity": 1,
                    "font-weight": 600,
                    "font-size": 11,
                    "text-max-width": "90px",
                    "text-valign": "center",
                    "text-halign": "center",
                    color: "#f8fafc",
                    width: 108,
                    height: 52,
                  },
                },
              ]
            : []),
          {
            selector: "edge",
            style: useNamedEdges
              ? {
                  "curve-style": "bezier",
                  "target-arrow-shape": "triangle",
                  "font-size": 8,
                  "text-rotation": "autorotate",
                  "text-margin-y": 0,
                  "text-background-color": "#ffffff",
                  "text-background-opacity": 0.92,
                  "text-background-padding": 3,
                  "min-zoomed-font-size": 6,
                  "text-halign": "center",
                  "text-valign": "center",
                }
              : {
                  "curve-style": "bezier",
                  "target-arrow-shape": "triangle",
                },
          },
          ...(useOrdine
            ? [
                {
                  selector: 'edge[tipo = "SUCCESSIONE_ZONA"]',
                  style: {
                    label: "data(riassunto_transizione)",
                    "text-wrap": "ellipsis",
                    "text-max-width": "140px",
                    "font-size": 8,
                    "text-rotation": "autorotate",
                    "text-margin-y": -6,
                    color: "#334155",
                  },
                },
              ]
            : []),
        ] as never,
      }) as unknown as CyLike;

      const host = containerRef.current;
      if (host) {
        instance.on(
          "mouseover",
          "edge",
          (evt: { target: { data: (key: string) => unknown } }) => {
            const tip =
              evt.target.data("title") ??
              evt.target.data("spiegazione") ??
              evt.target.data("riassunto_transizione");
            if (tip) host.title = String(tip);
          },
        );
        instance.on("mouseout", "edge", () => {
          host.title = "";
        });
        instance.on(
          "mouseover",
          "node",
          (evt: { target: { data: (key: string) => unknown } }) => {
            if (useOrdine && String(evt.target.data("tipo") ?? "") === "Zona") {
              host.style.cursor = "pointer";
            }
            const tipo = String(evt.target.data("tipo") ?? "");
            if (
              useTemporale &&
              (tipo === "AncoraTemporale" || tipo === "ClusterTemporale")
            ) {
              host.style.cursor = "pointer";
            }
            const tip =
              evt.target.data("title") ?? evt.target.data("descrizione");
            if (tip) host.title = String(tip);
          },
        );
        instance.on("mouseout", "node", () => {
          host.title = "";
          host.style.cursor = "";
        });
        instance.on("tap", "node", (evt: { target: { id: () => string } }) => {
          onSelectRef.current?.({ kind: "nodo", id: String(evt.target.id()) });
        });
        instance.on("tap", "edge", (evt: { target: { id: () => string } }) => {
          onSelectRef.current?.({ kind: "arco", id: String(evt.target.id()) });
        });
        instance.on("tap", (evt: { target: unknown }) => {
          if (evt.target === instance) onSelectRef.current?.(null);
        });
      }

      cy = instance;
      cyRef.current = instance;
      fittedRef.current = false;
      nodeSigRef.current.clear();
      edgeSigRef.current.clear();
      setCyEpoch((value) => value + 1);
    }

    void mount();
    return () => {
      cancelled = true;
      queueRef.current = [];
      pumpingRef.current = false;
      cyRef.current = null;
      cy?.destroy();
    };
  }, [layout]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    const useOrdine = layout === "ordine";
    const useCose = layout === "cose";
    const useTemporale = layout === "temporale";
    const useEntita = layout === "entita";
    const useNamedEdges = !useOrdine && !useTemporale;
    const displayedIds = new Set(nodes.map((node) => node.data.id));
    const hasSuccessioneAncora = edges.some(
      (edge) => String(edge.data?.tipo ?? "").toUpperCase() === "SUCCESSIONE_ANCORA",
    );
    const cyEdges =
      useTemporale && !hasSuccessioneAncora
        ? [...edges, ...timelineRankEdges(nodes)]
        : edges;
    const presetPositions = useOrdine
      ? positionsOrdine({ nodes, edges })
      : useEntita
        ? positionsEntita({ nodes, edges })
        : null;

    const currentNodeIds: string[] = [];
    cy.nodes().forEach((ele) => currentNodeIds.push(String(ele.id())));
    const currentEdgeIds: string[] = [];
    cy.edges().forEach((ele) => currentEdgeIds.push(String(ele.id())));

    const wantedNodeIds = nodes.map((node) => node.data.id);
    const wantedEdgeIds = cyEdges.map((edge) => edge.data.id);
    const nodeDiff = diffIds(currentNodeIds, wantedNodeIds);
    const edgeDiff = diffIds(currentEdgeIds, wantedEdgeIds);

    const nodesById = new Map(nodes.map((node) => [node.data.id, node]));
    const edgesById = new Map(cyEdges.map((edge) => [edge.data.id, edge]));
    const removeNodeObjs = nodeDiff.remove
      .map((id) => nodesById.get(id))
      .filter((node): node is (typeof nodes)[number] => node != null);
    const orderedRemove = sortNodesChildrenFirst(
      removeNodeObjs.length === nodeDiff.remove.length
        ? removeNodeObjs
        : nodeDiff.remove.map((id) => ({
            data: { id, label: id, tipo: "Fatto" },
          })),
    );
    const addNodeObjs = sortNodesParentsFirst(
      nodeDiff.add
        .map((id) => nodesById.get(id))
        .filter((node): node is (typeof nodes)[number] => node != null),
    );

    queueRef.current = [];
    const enqueue = (fn: () => void) => {
      queueRef.current.push(fn);
    };

    for (const chunk of chunkArray(edgeDiff.remove, LIVE_GRAPH_BATCH_SIZE)) {
      enqueue(() => {
        cy.batch(() => {
          for (const id of chunk) {
            if (cyHas(cy, id)) cy.getElementById(id).remove();
            edgeSigRef.current.delete(id);
          }
        });
      });
    }
    for (const chunk of chunkArray(
      orderedRemove.map((node) => node.data.id),
      LIVE_GRAPH_BATCH_SIZE,
    )) {
      enqueue(() => {
        cy.batch(() => {
          for (const id of chunk) {
            if (cyHas(cy, id)) cy.getElementById(id).remove();
            nodeSigRef.current.delete(id);
          }
        });
      });
    }

    for (const chunk of chunkArray(addNodeObjs, LIVE_GRAPH_BATCH_SIZE)) {
      enqueue(() => {
        const payload = chunk.map((node, index) => {
          const globalIndex = wantedNodeIds.indexOf(node.data.id);
          const position =
            presetPositions?.[node.data.id] ??
            gridPosition(globalIndex >= 0 ? globalIndex : index);
          return {
            group: "nodes" as const,
            data: prepareNodeData(node.data, layout, displayedIds),
            position,
          };
        });
        cy.batch(() => {
          const added = cy.add(payload);
          added.forEach((ele) => {
            styleNode(ele, useOrdine);
            const raw = nodesById.get(String(ele.id()));
            if (raw) nodeSigRef.current.set(raw.data.id, nodeSignature(raw.data));
          });
        });
      });
    }

    const addEdges = edgeDiff.add
      .map((id) => edgesById.get(id))
      .filter((edge): edge is (typeof cyEdges)[number] => edge != null);
    for (const chunk of chunkArray(addEdges, LIVE_GRAPH_BATCH_SIZE)) {
      enqueue(() => {
        const payload = chunk
          .filter(
            (edge) =>
              cyHas(cy, edge.data.source) && cyHas(cy, edge.data.target),
          )
          .map((edge) => ({
            group: "edges" as const,
            data: { ...edge.data },
          }));
        if (payload.length === 0) return;
        cy.batch(() => {
          const added = cy.add(payload);
          added.forEach((ele) => {
            styleEdge(ele, useNamedEdges);
            edgeSigRef.current.set(String(ele.id()), edgeSignature(ele as never));
          });
        });
        for (const edge of chunk) {
          edgeSigRef.current.set(edge.data.id, edgeSignature(edge.data));
        }
      });
    }

    const stayNodeIds = wantedNodeIds.filter((id) => !nodeDiff.add.includes(id));
    for (const chunk of chunkArray(stayNodeIds, LIVE_GRAPH_BATCH_SIZE)) {
      enqueue(() => {
        cy.batch(() => {
          for (const id of chunk) {
            const node = nodesById.get(id);
            if (!node || !cyHas(cy, id)) continue;
            const sig = nodeSignature(node.data);
            const position = presetPositions?.[id];
            if (nodeSigRef.current.get(id) !== sig) {
              cy.getElementById(id).data(
                prepareNodeData(node.data, layout, displayedIds),
              );
              nodeSigRef.current.set(id, sig);
            }
            if (position) cy.getElementById(id).position(position);
            styleNode(cy.getElementById(id), useOrdine);
          }
        });
      });
    }

    const stayEdgeIds = wantedEdgeIds.filter((id) => !edgeDiff.add.includes(id));
    for (const chunk of chunkArray(stayEdgeIds, LIVE_GRAPH_BATCH_SIZE)) {
      enqueue(() => {
        cy.batch(() => {
          for (const id of chunk) {
            const edge = edgesById.get(id);
            if (!edge || !cyHas(cy, id)) continue;
            const sig = edgeSignature(edge.data);
            if (edgeSigRef.current.get(id) !== sig) {
              cy.getElementById(id).data({ ...edge.data });
              edgeSigRef.current.set(id, sig);
            }
            styleEdge(cy.getElementById(id), useNamedEdges);
          }
        });
      });
    }

    enqueue(() => {
      const count = wantedNodeIds.length;
      const useHeavy = shouldUseHeavyLayout(count, liveRef.current);
      if (useOrdine) {
        if (!fittedRef.current && count > 0) {
          cy.fit(undefined, useCose ? 40 : 24);
          fittedRef.current = true;
        }
        return;
      }
      if (useHeavy) {
        const firstCose = useCose && !fittedRef.current;
        const layoutOpts = useCose
          ? coseRelazioniLayoutOptions(firstCose)
          : {
              name: "dagre",
              rankDir: useTemporale ? "LR" : "TB",
              nodeSep: 48,
              rankSep: 72,
              padding: 24,
              animate: false,
              fit: false,
            };
        try {
          cy.layout(layoutOpts).run();
        } catch {
          /* keep the batched positions */
        }
        cy.fit(undefined, useCose ? 40 : 24);
        fittedRef.current = true;
        return;
      }
      if (!fittedRef.current && count > 0) {
        cy.fit(undefined, 24);
        fittedRef.current = true;
      }
    });

    pump();
  }, [nodes, edges, layout, highlights, legendFilter, live, cyEpoch]);

  return (
    <div className={cn("relative h-full min-h-[20rem] w-full", className)}>
      <div
        ref={containerRef}
        className="h-full min-h-[20rem] w-full"
        role="img"
        aria-label="Grafo degli eventi"
      />
      {isEmpty ? (
        <p
          className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-muted-foreground"
          role="status"
        >
          Nessun elemento nel grafo.
        </p>
      ) : null}
    </div>
  );
}
