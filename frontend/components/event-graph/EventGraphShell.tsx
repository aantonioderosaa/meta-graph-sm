"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";

import { ElementInspector } from "@/components/event-graph/ElementInspector";
import { EventIngestPanel } from "@/components/event-graph/EventIngestPanel";
import { EventLegend } from "@/components/event-graph/EventLegend";
import { EventPipelineMonitor } from "@/components/event-graph/EventPipelineMonitor";
import { EventQueryPanel } from "@/components/event-graph/EventQueryPanel";
import { fetchCatalog, fetchGraph, fetchStats } from "@/lib/event-graph/api";
import type { HighlightKind } from "@/lib/event-graph/highlight";
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
          <EventGraphPanel
            elements={elements}
            highlights={highlights}
            legendFilter={legendFilter}
            layout={LAYOUT_BY_VISTA[vista]}
            onSelect={setSelection}
            className="h-full"
          />
        </main>
        <aside className="flex w-full shrink-0 flex-col gap-3 overflow-y-auto border-t border-border p-3 lg:w-80 lg:border-l lg:border-t-0">
          <ElementInspector
            selection={selection}
            elements={elements}
            onSelectRelated={setSelection}
          />
          <EventLegend
            catalog={catalog}
            stats={stats}
            filter={legendFilter}
            onFilterChange={setLegendFilter}
          />
          <EventQueryPanel onHighlightsChange={setHighlights} />
          <EventIngestPanel onJobStarted={setJobId} onGraphReset={loadGraph} />
          <EventPipelineMonitor jobId={jobId} onDone={loadGraph} />
        </aside>
      </div>
    </div>
  );
}
