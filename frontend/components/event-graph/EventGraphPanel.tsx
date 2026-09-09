"use client";

import { useEffect, useRef } from "react";

import { encodeEdge, encodeNode } from "@/lib/event-graph/encoding";
import {
  HIGHLIGHT_COLORS,
  type HighlightKind,
} from "@/lib/event-graph/highlight";
import { legendOpacity, type LegendFilter } from "@/lib/event-graph/legend";
import type {
  ElementSelection,
  EventGraphElements,
} from "@/lib/event-graph/types";
import { cn } from "@/lib/utils";

let dagreRegistered = false;

type EventGraphPanelProps = {
  elements?: EventGraphElements | null;
  highlights?: Record<string, HighlightKind>;
  legendFilter?: LegendFilter;
  onSelect?: (selection: ElementSelection | null) => void;
  className?: string;
};

export function EventGraphPanel({
  elements,
  highlights,
  legendFilter = null,
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

      const instance = cytoscape({
        container: containerRef.current,
        elements: [
          ...nodes.map((node) => ({ data: { ...node.data }, group: "nodes" as const })),
          ...edges.map((edge) => ({ data: { ...edge.data }, group: "edges" as const })),
        ],
        layout: {
          name: "dagre",
          rankDir: "TB",
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
            selector: "edge",
            style: {
              "curve-style": "bezier",
              "target-arrow-shape": "triangle",
            },
          },
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
        };
        const style = encodeNode(nodeData);
        const highlight = highlights?.[String(ele.id())];
        ele.style({
          "background-color": style.color,
          "border-color": style.borderColor,
          "border-width": style.borderWidth,
          "border-style": style.borderStyle,
          shape: style.shape,
          "background-opacity": style.filled ? 1 : 0.15,
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
        const edgeData = {
          id: String(ele.id()),
          source: String(ele.data("source") ?? ""),
          target: String(ele.data("target") ?? ""),
          tipo: String(ele.data("tipo") ?? ""),
          base: ele.data("base"),
          segnale: ele.data("segnale"),
          superato_da: ele.data("superato_da"),
          conflitto: ele.data("conflitto"),
        };
        const style = encodeEdge(edgeData);
        ele.style({
          "line-color": style.color,
          "target-arrow-color": style.color,
          width: style.double ? Math.max(style.width, 5) : style.width,
          "line-style": style.lineStyle,
          "arrow-scale": style.markedArrow ? 1.6 : 1,
          opacity: legendOpacity(style.opacity, { data: edgeData }, legendFilter),
          "line-outline-width": style.borderColor ? 2 : 0,
          "line-outline-color": style.borderColor ?? "transparent",
        });
      });

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
  }, [isEmpty, nodes, edges, highlights, legendFilter, onSelect]);

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
