"use client";

import { useEffect, useRef } from "react";

import { encodeEdge, encodeNode } from "@/lib/event-graph/encoding";
import {
  HIGHLIGHT_COLORS,
  type HighlightKind,
} from "@/lib/event-graph/highlight";
import {
  positionsOrdine,
  zonaDisplayLabel,
} from "@/lib/event-graph/layout-ordine";
import { ladderEdgeLabel } from "@/lib/event-graph/layout-zigzag";
import {
  isTimelineRankEdgeId,
  timelineRankEdges,
} from "@/lib/event-graph/layout-temporale";
import { legendOpacity, type LegendFilter } from "@/lib/event-graph/legend";
import type {
  ElementSelection,
  EventGraphElements,
} from "@/lib/event-graph/types";
import { cn } from "@/lib/utils";

let dagreRegistered = false;

export type EventGraphLayout = "dagre" | "ordine" | "temporale" | "cose";

type EventGraphPanelProps = {
  elements?: EventGraphElements | null;
  highlights?: Record<string, HighlightKind>;
  legendFilter?: LegendFilter;
  layout?: EventGraphLayout;
  onSelect?: (selection: ElementSelection | null) => void;
  className?: string;
};

export function EventGraphPanel({
  elements,
  highlights,
  legendFilter = null,
  layout = "dagre",
  onSelect,
  className,
}: EventGraphPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const nodes = elements?.nodes ?? [];
  const edges = elements?.edges ?? [];
  const isEmpty = nodes.length === 0 && edges.length === 0;

  useEffect(() => {
    if (isEmpty) return;
    const container = containerRef.current;
    if (!container) return;

    let cancelled = false;
    let cy: {
      destroy: () => void;
      fit: (eles?: unknown, padding?: number) => void;
      on: (event: string, ...rest: unknown[]) => void;
    } | null = null;

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
      const presetPositions = useOrdine ? positionsOrdine({ nodes, edges }) : null;
      const displayedIds = new Set(nodes.map((node) => node.data.id));
      const hasSuccessioneAncora = edges.some(
        (edge) => String(edge.data?.tipo ?? "").toUpperCase() === "SUCCESSIONE_ANCORA",
      );
      const cyEdges =
        useTemporale && !hasSuccessioneAncora
          ? [...edges, ...timelineRankEdges(nodes)]
          : edges;

      const instance = cytoscape({
        container: containerRef.current,
        elements: [
          ...nodes.map((node) => {
            const data = { ...node.data };
            if (useOrdine && data.parent) {
              data.zona_id = data.parent;
            }
            if (
              useOrdine ||
              useCose ||
              data.parent == null ||
              data.parent === "" ||
              (useTemporale && !displayedIds.has(String(data.parent)))
            ) {
              delete data.parent;
            }
            const position = presetPositions?.[data.id];
            return {
              data,
              group: "nodes" as const,
              ...(position ? { position } : {}),
            };
          }),
          ...cyEdges.map((edge) => ({ data: { ...edge.data }, group: "edges" as const })),
        ],
        layout: useOrdine
          ? { name: "preset", fit: true, padding: 24 }
          : useCose
            ? { name: "cose", padding: 24, animate: false, fit: true }
            : {
                name: "dagre",
                rankDir: useTemporale ? "LR" : "TB",
                nodeSep: 48,
                rankSep: 72,
                padding: 24,
              },
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
              ? 'node:parent, node[tipo = "AncoraTemporale"], node[tipo = "ClusterTemporale"]'
              : 'node:parent, node[tipo = "Zona"], node[tipo = "AncoraTemporale"], node[tipo = "ClusterTemporale"]',
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
        ],
      });

      instance.nodes().forEach((ele) => {
        const nodeData = {
          id: String(ele.id()),
          label: String(ele.data("label") ?? ele.id()),
          tipo: String(ele.data("tipo") ?? "Evento"),
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
        };
        const style = encodeNode(nodeData);
        const highlight = highlights?.[String(ele.id())];
        const isOrdineZona = useOrdine && nodeData.tipo === "Zona";
        const isAncora =
          nodeData.tipo === "AncoraTemporale" ||
          nodeData.tipo === "ClusterTemporale";
        const isZonaHub = nodeData.tipo === "Zona" && !useOrdine;
        const isCompoundParent =
          typeof (ele as { isParent?: () => boolean }).isParent === "function" &&
          Boolean((ele as { isParent: () => boolean }).isParent());
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
          ? zonaDisplayLabel(nodeData)
          : etichetta || lemma;
        const riassuntoRaw = ele.data("riassunto");
        const riassunto =
          riassuntoRaw != null && String(riassuntoRaw) !== ""
            ? String(riassuntoRaw)
            : "";
        if (isOrdineZona && (riassunto || nodeData.label)) {
          ele.data("title", riassunto || String(nodeData.label));
        } else if (descrizione) {
          ele.data(
            "title",
            etichetta && etichetta !== descrizione
              ? `${etichetta} · ${descrizione}`
              : descrizione,
          );
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
          opacity: legendOpacity(style.opacity, { data: nodeData }, legendFilter),
        });
        if (highlight) {
          ele.style({
            "overlay-padding": 8,
            "overlay-color": HIGHLIGHT_COLORS[highlight],
            "overlay-opacity": 0.55,
            "border-width": Math.max(style.borderWidth, 2) + 3,
          });
        }
      });

      instance.edges().forEach((ele) => {
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
        const style = encodeEdge(edgeData);
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
          opacity: legendOpacity(style.opacity, { data: edgeData }, legendFilter),
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
            : {}),
        });
      });

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
      }

      instance.fit(undefined, 24);

      if (onSelect) {
        instance.on("tap", "node", (evt: { target: { id: () => string } }) => {
          onSelect({ kind: "nodo", id: String(evt.target.id()) });
        });
        instance.on("tap", "edge", (evt: { target: { id: () => string } }) => {
          onSelect({ kind: "arco", id: String(evt.target.id()) });
        });
        instance.on("tap", (evt: { target: unknown }) => {
          if (evt.target === instance) onSelect(null);
        });
      }

      cy = instance;
    }

    void mount();
    return () => {
      cancelled = true;
      cy?.destroy();
    };
  }, [isEmpty, nodes, edges, highlights, legendFilter, layout, onSelect]);

  if (isEmpty) {
    return (
      <div
        className={cn(
          "flex h-full min-h-[20rem] items-center justify-center rounded-lg border border-dashed border-border bg-muted/30 text-sm text-muted-foreground",
          className,
        )}
        role="status"
      >
        Nessun elemento nel grafo.
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={cn("h-full min-h-[20rem] w-full", className)}
      role="img"
      aria-label="Grafo degli eventi"
    />
  );
}
