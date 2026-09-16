"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ElementInspector } from "@/components/event-graph/ElementInspector";
import { EventIngestPanel } from "@/components/event-graph/EventIngestPanel";
import { EventLegend } from "@/components/event-graph/EventLegend";
import { EventPipelineMonitor } from "@/components/event-graph/EventPipelineMonitor";
import { EventQueryPanel } from "@/components/event-graph/EventQueryPanel";
import { fetchCatalog, fetchGraph, fetchStats } from "@/lib/event-graph/api";
import type { HighlightKind } from "@/lib/event-graph/highlight";
import {
  filterOrdineElements,
  zonaDisplayLabel,
} from "@/lib/event-graph/layout-ordine";
import {
  ancoraDisplayLabel,
  filterTemporaleElements,
  isAncoraTemporaleNode,
} from "@/lib/event-graph/layout-temporale";
import { EMPTY_STATS, type LegendFilter } from "@/lib/event-graph/legend";
import type {
  ElementSelection,
  EventGraphCatalog,
  EventGraphElements,
  EventGraphStats,
  GraphFilters,
} from "@/lib/event-graph/types";
import { cn } from "@/lib/utils";

type VistaGraph = NonNullable<GraphFilters["vista"]>;

const VISTA_BUTTONS: { id: VistaGraph; label: string }[] = [
  { id: "tutto", label: "Tutto" },
  { id: "ordine", label: "Ordine" },
  { id: "temporale", label: "Temporale" },
  { id: "relazioni", label: "Relazioni" },
];

const LAYOUT_BY_VISTA = {
  tutto: "dagre",
  ordine: "ordine",
  temporale: "temporale",
  relazioni: "cose",
} as const;

const EventGraphPanel = dynamic(
  () =>
    import("@/components/event-graph/EventGraphPanel").then(
      (mod) => mod.EventGraphPanel,
    ),
  { ssr: false },
);

export function EventGraphShell() {
  const [elements, setElements] = useState<EventGraphElements | null>(null);
  const [catalog, setCatalog] = useState<EventGraphCatalog | null>(null);
  const [stats, setStats] = useState<EventGraphStats>(EMPTY_STATS);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [highlights, setHighlights] = useState<Record<string, HighlightKind>>(
    {},
  );
  const [legendFilter, setLegendFilter] = useState<LegendFilter>(null);
  const [selection, setSelection] = useState<ElementSelection | null>(null);
  const [vista, setVista] = useState<VistaGraph>("tutto");
  const [focusedZonaId, setFocusedZonaId] = useState<string | null>(null);
  const [ancoraPath, setAncoraPath] = useState<string[]>([]);

  const loadGraph = useCallback(async () => {
    try {
      const graph = await fetchGraph(
        vista === "tutto" ? {} : { vista },
      );
      setElements(graph.elements);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Grafo non disponibile");
    }
    try {
      setCatalog(await fetchCatalog());
    } catch {
      /* catalog is static; keep the last successful payload */
    }
    try {
      setStats(await fetchStats());
    } catch {
      setStats(EMPTY_STATS);
    }
  }, [vista]);

  useEffect(() => {
    void loadGraph();
  }, [loadGraph]);

  useEffect(() => {
    setFocusedZonaId(null);
    setAncoraPath([]);
  }, [vista]);

  const displayElements = useMemo(() => {
    if (vista === "ordine") return filterOrdineElements(elements, focusedZonaId);
    if (vista === "temporale") {
      return filterTemporaleElements(elements, ancoraPath);
    }
    return elements;
  }, [vista, elements, focusedZonaId, ancoraPath]);

  const focusedZona = useMemo(() => {
    if (!focusedZonaId || !elements) return null;
    return (
      elements.nodes.find(
        (node) => node.data.id === focusedZonaId && node.data.tipo === "Zona",
      )?.data ?? null
    );
  }, [focusedZonaId, elements]);

  const ancoraCrumbs = useMemo(() => {
    if (vista !== "temporale" || ancoraPath.length === 0 || !elements) return [];
    return ancoraPath.map((id) => {
      const node = elements.nodes.find((item) => item.data.id === id);
      return { id, label: ancoraDisplayLabel(node) || id };
    });
  }, [vista, ancoraPath, elements]);

  const nessunaAncoraTemporale = useMemo(() => {
    if (vista !== "temporale" || elements == null) return false;
    return !elements.nodes.some((node) => isAncoraTemporaleNode(node));
  }, [vista, elements]);

  const handleSelect = useCallback(
    (sel: ElementSelection | null) => {
      setSelection(sel);
      if (sel?.kind !== "nodo") return;
      const node = elements?.nodes.find((item) => item.data.id === sel.id);
      const tipo = node?.data.tipo;
      if (vista === "ordine" && String(tipo ?? "") === "Zona") {
        setFocusedZonaId(sel.id);
        return;
      }
      if (vista === "temporale" && isAncoraTemporaleNode(node)) {
        setAncoraPath((prev) => {
          if (prev[prev.length - 1] === sel.id) return prev;
          return [...prev, sel.id];
        });
      }
    },
    [vista, elements],
  );

  return (
    <div className="flex h-screen min-h-0 flex-col bg-background text-foreground">
      <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Grafo degli eventi</h1>
          <p className="text-xs text-muted-foreground">
            Ingestione, pipeline e visualizzazione del grafo degli eventi
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">Vista</span>
          <div
            role="group"
            aria-label="Vista del grafo"
            className="inline-flex overflow-hidden rounded-md border border-border text-xs"
          >
            {VISTA_BUTTONS.map((option) => (
              <button
                key={option.id}
                type="button"
                className={cn(
                  "px-2.5 py-1",
                  vista === option.id
                    ? "bg-muted font-medium text-foreground"
                    : "bg-background text-muted-foreground hover:bg-muted/50",
                )}
                aria-pressed={vista === option.id}
                onClick={() => setVista(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <main className="min-h-0 min-w-0 flex-1 p-3">
          {error ? (
            <p className="mb-2 text-xs text-destructive" role="alert">
              {error}
            </p>
          ) : null}
          <div className="relative h-full min-h-0">
            {vista === "ordine" && focusedZona ? (
              <div className="absolute left-2 top-2 z-10 flex max-w-[min(100%,28rem)] items-center gap-2 rounded-md border border-border bg-background/95 px-2 py-1 shadow-sm">
                <button
                  type="button"
                  className="shrink-0 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                  onClick={() => {
                    setFocusedZonaId(null);
                    setSelection(null);
                  }}
                >
                  ← Tutte le zone
                </button>
                <span
                  className="truncate text-xs"
                  title={
                    focusedZona.riassunto ?? focusedZona.label ?? undefined
                  }
                >
                  {zonaDisplayLabel(focusedZona)}
                  {focusedZona.riassunto
                    ? ` · ${focusedZona.riassunto}`
                    : ""}
                </span>
              </div>
            ) : null}
            {vista === "temporale" && ancoraCrumbs.length > 0 ? (
              <div className="absolute left-2 top-2 z-10 flex max-w-[min(100%,36rem)] flex-wrap items-center gap-1 rounded-md border border-border bg-background/95 px-2 py-1 shadow-sm">
                <button
                  type="button"
                  className="shrink-0 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                  onClick={() => {
                    setAncoraPath([]);
                    setSelection(null);
                  }}
                >
                  ← Tutte le ancore
                </button>
                {ancoraCrumbs.map((crumb, index) => (
                  <span key={crumb.id} className="flex min-w-0 items-center gap-1">
                    <span className="text-xs text-muted-foreground" aria-hidden>
                      /
                    </span>
                    <button
                      type="button"
                      className="truncate rounded px-1 py-0.5 text-xs hover:bg-muted hover:text-foreground"
                      title={crumb.label}
                      onClick={() => {
                        setAncoraPath(ancoraPath.slice(0, index + 1));
                        setSelection(null);
                      }}
                    >
                      {crumb.label}
                    </button>
                  </span>
                ))}
              </div>
            ) : null}
            {nessunaAncoraTemporale ? (
              <p
                className="flex h-full items-center justify-center text-sm text-muted-foreground"
                role="status"
              >
                nessuna ancora temporale in questo documento
              </p>
            ) : (
              <EventGraphPanel
                elements={displayElements}
                highlights={highlights}
                legendFilter={legendFilter}
                layout={LAYOUT_BY_VISTA[vista]}
                onSelect={handleSelect}
                className="h-full"
              />
            )}
          </div>
        </main>
        <aside className="flex w-full shrink-0 flex-col gap-3 overflow-y-auto border-t border-border p-3 lg:w-80 lg:border-l lg:border-t-0">
          <ElementInspector
            selection={selection}
            elements={elements}
            onSelectRelated={handleSelect}
          />
          <EventLegend
            catalog={catalog}
            stats={stats}
            filter={legendFilter}
            onFilterChange={setLegendFilter}
          />
          <EventQueryPanel onHighlightsChange={setHighlights} />
          <EventIngestPanel onJobStarted={setJobId} />
          <EventPipelineMonitor jobId={jobId} onDone={loadGraph} />
        </aside>
      </div>
    </div>
  );
}
