"use client";

/**
 * Graph Explorer with @neo4j-nvl/react (tech-spec §11.1, E7).
 * Always hits real GET /graph /facts endpoints (E7.6 — no fixtures here).
 */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { MouseEventCallbacks } from "@neo4j-nvl/react";

import { FactDetailPanel } from "@/components/FactDetailPanel";
import { Button } from "@/components/ui/button";
import { getFactHistory, getGraph } from "@/lib/api-client";
import { toNvlGraph } from "@/lib/graph-encoding";
import { useAppStore } from "@/lib/store";

const InteractiveNvlWrapper = dynamic(
  () =>
    import("@neo4j-nvl/react").then((mod) => mod.InteractiveNvlWrapper),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Caricamento visualizzazione…
      </div>
    ),
  },
);

export function GraphExplorer() {
  const nodes = useAppStore((s) => s.nodes);
  const relationships = useAppStore((s) => s.relationships);
  const selectedFactId = useAppStore((s) => s.selectedFactId);
  const historyHighlight = useAppStore((s) => s.historyHighlight);
  const onlyLatest = useAppStore((s) => s.onlyLatest);
  const querySubgraph = useAppStore((s) => s.querySubgraph);
  const lastPipelineEvent = useAppStore((s) => s.lastPipelineEvent);
  const pulsingIds = useAppStore((s) => s.pulsingIds);
  const setGraph = useAppStore((s) => s.setGraph);
  const setSelectedFactId = useAppStore((s) => s.setSelectedFactId);
  const setHistoryHighlight = useAppStore((s) => s.setHistoryHighlight);
  const setOnlyLatest = useAppStore((s) => s.setOnlyLatest);
  const pulseEntities = useAppStore((s) => s.pulseEntities);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const loadGraph = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getGraph({ is_latest: onlyLatest });
      // Chunk nodes are excluded by the backend; defensive filter kept for safety.
      const safeNodes = data.nodes.filter(
        (n) => !String(n.id).toLowerCase().startsWith("chunk"),
      );
      setGraph(safeNodes, data.relationships);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Impossibile caricare il grafo");
      setGraph([], []);
    } finally {
      setLoading(false);
    }
  }, [onlyLatest, setGraph]);

  useEffect(() => {
    void loadGraph();
  }, [loadGraph, reloadToken]);

  // Pulse nodes/edges referenced by pipeline events (E8.3). Missing ids → no-op.
  useEffect(() => {
    if (!lastPipelineEvent) return;
    const payload = lastPipelineEvent.payload;
    const candidates: string[] = [];
    for (const key of ["fact_id", "src", "tgt"] as const) {
      const value = payload[key];
      if (typeof value === "string") candidates.push(value);
    }
    const factIds = payload.fact_ids;
    if (Array.isArray(factIds)) {
      for (const id of factIds) {
        if (typeof id === "string") candidates.push(id);
      }
    }
    if (candidates.length === 0) return;

    const present = new Set([
      ...nodes.map((n) => n.id),
      ...relationships.map((r) => r.id),
    ]);
    const toPulse = candidates.filter((id) => present.has(id));
    if (toPulse.length > 0) {
      pulseEntities(toPulse);
    }
  }, [lastPipelineEvent, nodes, relationships, pulseEntities]);

  const historyNodeIds = useMemo(
    () => (historyHighlight ? new Set(historyHighlight.nodeIds) : null),
    [historyHighlight],
  );
  const historyRelIds = useMemo(
    () => (historyHighlight ? new Set(historyHighlight.relIds) : null),
    [historyHighlight],
  );
  const queryNodeIds = useMemo(
    () =>
      querySubgraph
        ? new Set(querySubgraph.nodes.map((n) => n.id))
        : null,
    [querySubgraph],
  );
  const queryRelKeys = useMemo(() => {
    if (!querySubgraph) return null;
    return new Set(
      querySubgraph.relationships.map(
        (r) => `${r.source}->${r.target}:${r.type.toLowerCase()}`,
      ),
    );
  }, [querySubgraph]);
  const pulsingIdSet = useMemo(() => new Set(pulsingIds), [pulsingIds]);

  const { nodes: nvlNodes, rels: nvlRels } = useMemo(
    () =>
      toNvlGraph(nodes, relationships, {
        selectedId: selectedFactId,
        historyNodeIds,
        historyRelIds,
        queryNodeIds,
        queryRelKeys,
        pulsingIds: pulsingIdSet,
      }),
    [
      nodes,
      relationships,
      selectedFactId,
      historyNodeIds,
      historyRelIds,
      queryNodeIds,
      queryRelKeys,
      pulsingIdSet,
    ],
  );

  const onNodeClick = useCallback(
    (node: { id: string }) => {
      setSelectedFactId(node.id);
    },
    [setSelectedFactId],
  );

  const onNodeDoubleClick = useCallback(
    async (node: { id: string }) => {
      setSelectedFactId(node.id);
      try {
        const history = await getFactHistory(node.id);
        const nodeIds = history.facts.map((f) => f.id);
        const idSet = new Set(nodeIds);
        const relIds = relationships
          .filter(
            (r) =>
              r.type.toUpperCase() === "UPDATES" &&
              idSet.has(r.from) &&
              idSet.has(r.to),
          )
          .map((r) => r.id);
        setHistoryHighlight({ nodeIds, relIds });
      } catch {
        setHistoryHighlight(null);
      }
    },
    [relationships, setHistoryHighlight, setSelectedFactId],
  );

  const onCanvasClick = useCallback(() => {
    setSelectedFactId(null);
  }, [setSelectedFactId]);

  const mouseEventCallbacks: MouseEventCallbacks = useMemo(
    () => ({
      onNodeClick,
      onNodeDoubleClick,
      onCanvasClick,
      onPan: true,
      onZoom: true,
      onDrag: true,
    }),
    [onCanvasClick, onNodeClick, onNodeDoubleClick],
  );

  return (
    <section
      aria-label="Graph Explorer"
      className="relative flex h-full min-h-[280px] flex-col overflow-hidden rounded-lg border border-border bg-background"
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 px-3 py-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-medium tracking-wide">Graph Explorer</h2>
          <span className="text-xs text-muted-foreground">
            {nodes.length} nodi · {relationships.length} rel
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              className="accent-foreground"
              checked={onlyLatest}
              onChange={(e) => {
                setOnlyLatest(e.target.checked);
                setHistoryHighlight(null);
              }}
            />
            Solo correnti
          </label>
          {historyHighlight ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setHistoryHighlight(null)}
            >
              Pulisci catena
            </Button>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setReloadToken((t) => t + 1)}
            disabled={loading}
          >
            Ricarica
          </Button>
        </div>
      </header>

      <div className="relative min-h-0 flex-1">
        {error ? (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-background/80 p-4 text-center text-sm">
            <p className="text-destructive">{error}</p>
            <p className="text-xs text-muted-foreground">
              Avvia il backend (`NEXT_PUBLIC_API_URL`) e riprova.
            </p>
            <Button size="sm" variant="outline" onClick={() => setReloadToken((t) => t + 1)}>
              Riprova
            </Button>
          </div>
        ) : null}
        {loading && nodes.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Caricamento grafo…
          </div>
        ) : nodes.length === 0 && !error ? (
          <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
            Nessun fatto nel grafo. Ingerisci un documento e avvia il dreaming.
          </div>
        ) : (
          <InteractiveNvlWrapper
            nodes={nvlNodes}
            rels={nvlRels}
            mouseEventCallbacks={mouseEventCallbacks}
            nvlOptions={{
              disableTelemetry: true,
              initialZoom: 1,
            }}
            style={{ width: "100%", height: "100%", minHeight: 280 }}
          />
        )}
        <FactDetailPanel />
      </div>

      <footer className="flex flex-wrap gap-3 border-t border-border/60 px-3 py-1.5 text-[10px] text-muted-foreground">
        <span>
          <i className="mr-1 inline-block h-2 w-2 rounded-full" style={{ background: "#0F766E" }} />
          fact
        </span>
        <span>
          <i className="mr-1 inline-block h-2 w-2 rounded-full" style={{ background: "#B45309" }} />
          preference
        </span>
        <span>
          <i className="mr-1 inline-block h-2 w-2 rounded-full" style={{ background: "#475569" }} />
          episode
        </span>
        <span className="opacity-60">storico = opacità ridotta</span>
        <span style={{ color: "#D97706" }}>UPDATES</span>
        <span style={{ color: "#2563EB" }}>EXTENDS</span>
        <span style={{ color: "#16A34A" }}>DERIVES</span>
      </footer>
    </section>
  );
}
