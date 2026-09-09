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
} from "@/lib/event-graph/types";

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

  const loadGraph = useCallback(async () => {
    try {
      const graph = await fetchGraph();
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
  }, []);

  useEffect(() => {
    void loadGraph();
  }, [loadGraph]);

  return (
    <div className="flex h-screen min-h-0 flex-col bg-background text-foreground">
      <header className="border-b border-border px-4 py-3">
        <h1 className="text-lg font-semibold tracking-tight">Grafo degli eventi</h1>
        <p className="text-xs text-muted-foreground">
          Ingestione, pipeline e visualizzazione del grafo degli eventi
        </p>
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
            onSelect={setSelection}
            className="h-full"
          />
        </main>
        <aside className="flex w-full shrink-0 flex-col gap-3 overflow-y-auto border-t border-border p-3 lg:w-80 lg:border-l lg:border-t-0">
          <ElementInspector selection={selection} onSelectRelated={setSelection} />
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
