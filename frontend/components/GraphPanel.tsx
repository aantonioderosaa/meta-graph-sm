"use client";

/**
 * Self-contained NVL graph panel.
 *
 * Local useState for nodes / relationships / loading / error / reloadToken.
 * Reads lastPipelineEvent and kbResetEpoch for auto-refresh — never writes
 * graph data into the global store.
 */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEventCallbacks } from "@neo4j-nvl/react";

import { Button } from "@/components/ui/button";
import { toNvlGraph, type NvlNode } from "@/lib/graph-encoding";
import { useAppStore } from "@/lib/store";
import type { GraphNode, GraphRelationship, GraphResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

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

function hexToRgba(hex: string, alpha: number): string {
  const normalized = hex.replace("#", "");
  const r = Number.parseInt(normalized.slice(0, 2), 16);
  const g = Number.parseInt(normalized.slice(2, 4), 16);
  const b = Number.parseInt(normalized.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function applyColorForNode(
  nvlNodes: NvlNode[],
  sourceNodes: GraphNode[],
  colorForNode?: (node: GraphNode) => string,
): NvlNode[] {
  if (!colorForNode) return nvlNodes;
  const byId = new Map(sourceNodes.map((n) => [n.id, n]));
  return nvlNodes.map((nvl) => {
    const src = byId.get(nvl.id);
    if (!src) return nvl;
    const base = colorForNode(src);
    if (nvl.color.startsWith("rgba")) {
      const alphaMatch = nvl.color.match(
        /rgba\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\)/,
      );
      const alpha = alphaMatch ? Number(alphaMatch[1]) : 1;
      return { ...nvl, color: hexToRgba(base, alpha) };
    }
    return { ...nvl, color: base };
  });
}

export function GraphInitErrorOverlay({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-background/90 p-4 text-center text-sm">
      <p className="text-destructive">
        Impossibile inizializzare la visualizzazione.
      </p>
      <p className="text-xs text-muted-foreground">
        Il browser potrebbe aver esaurito i contesti grafici disponibili —
        chiudi altre schede/pannelli e ricarica.
      </p>
      <Button size="sm" variant="outline" onClick={onRetry}>
        Riprova
      </Button>
    </div>
  );
}

export interface GraphPanelProps {
  title: string;
  fetcher: () => Promise<GraphResponse>;
  className?: string;
  colorForNode?: (node: GraphNode) => string;
  onNodeClick?: (id: string, node: GraphNode) => void;
  /** Highlight matching nodes; do not filter the graph. */
  highlightIds?: Set<string> | null;
  selectedId?: string | null;
  emptyMessage?: string;
  /** Changing this value re-runs `fetcher` (e.g. concept-bridge toggle). */
  reloadKey?: unknown;
  onRelationshipClick?: (rel: GraphRelationship) => void;
}

export function GraphPanel({
  title,
  fetcher,
  className,
  colorForNode,
  onNodeClick,
  highlightIds = null,
  selectedId = null,
  emptyMessage = "Nessun nodo nel grafo.",
  reloadKey,
  onRelationshipClick,
}: GraphPanelProps) {
  const lastPipelineEvent = useAppStore((s) => s.lastPipelineEvent);
  const kbResetEpoch = useAppStore((s) => s.kbResetEpoch);

  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [relationships, setRelationships] = useState<GraphRelationship[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [initError, setInitError] = useState<string | null>(null);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const loadGraph = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetcherRef.current();
      const safeNodes = data.nodes.filter(
        (n) => !String(n.id).toLowerCase().startsWith("chunk"),
      );
      setNodes(safeNodes);
      setRelationships(data.relationships);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Impossibile caricare il grafo");
      setNodes([]);
      setRelationships([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadGraph();
  }, [loadGraph, reloadToken, reloadKey]);

  useEffect(() => {
    if (!lastPipelineEvent) return;
    if (
      lastPipelineEvent.stage === "done" &&
      lastPipelineEvent.event === "pipeline_complete"
    ) {
      setReloadToken((t) => t + 1);
    }
  }, [lastPipelineEvent]);

  useEffect(() => {
    if (kbResetEpoch === 0) return;
    setError(null);
    setReloadToken((t) => t + 1);
  }, [kbResetEpoch]);

  const retryVisualization = useCallback(() => {
    setInitError(null);
    setReloadToken((t) => t + 1);
  }, []);

  const { nodes: nvlNodes, rels: nvlRels } = useMemo(() => {
    const encoded = toNvlGraph(nodes, relationships, {
      selectedId,
      queryNodeIds: highlightIds,
    });
    return {
      nodes: applyColorForNode(encoded.nodes, nodes, colorForNode),
      rels: encoded.rels,
    };
  }, [nodes, relationships, selectedId, highlightIds, colorForNode]);

  const onNodeClickInternal = useCallback(
    (nvlNode: { id: string }) => {
      if (!onNodeClick) return;
      const node = nodes.find((n) => n.id === nvlNode.id);
      if (node) onNodeClick(nvlNode.id, node);
    },
    [onNodeClick, nodes],
  );

  const onRelationshipClickInternal = useCallback(
    (nvlRel: { id: string }) => {
      if (!onRelationshipClick) return;
      const rel = relationships.find((item) => item.id === nvlRel.id);
      if (rel) onRelationshipClick(rel);
    },
    [onRelationshipClick, relationships],
  );

  const mouseEventCallbacks: MouseEventCallbacks = useMemo(
    () => ({
      onNodeClick: onNodeClickInternal,
      onRelationshipClick: onRelationshipClickInternal,
      onPan: true,
      onZoom: true,
      onDrag: true,
    }),
    [onNodeClickInternal, onRelationshipClickInternal],
  );

  return (
    <section
      aria-label={title}
      className={cn(
        "relative flex h-full min-h-[220px] min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-background",
        className,
      )}
    >
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border/60 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="truncate text-sm font-medium tracking-wide">{title}</h2>
          <span className="shrink-0 text-xs text-muted-foreground">
            {nodes.length} nodi · {relationships.length} rel
          </span>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setInitError(null);
            setReloadToken((t) => t + 1);
          }}
          disabled={loading}
        >
          Ricarica
        </Button>
      </header>

      <div className="relative min-h-0 flex-1">
        {error ? (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-background/80 p-4 text-center text-sm">
            <p className="text-destructive">{error}</p>
            <p className="text-xs text-muted-foreground">
              Avvia il backend (`NEXT_PUBLIC_API_URL`) e riprova.
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setReloadToken((t) => t + 1)}
            >
              Riprova
            </Button>
          </div>
        ) : null}
        {initError ? (
          <GraphInitErrorOverlay onRetry={retryVisualization} />
        ) : null}
        {loading && nodes.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Caricamento grafo…
          </div>
        ) : nodes.length === 0 && !error ? (
          <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
            {emptyMessage}
          </div>
        ) : (
          <InteractiveNvlWrapper
            key={reloadToken}
            nodes={nvlNodes}
            rels={nvlRels}
            mouseEventCallbacks={mouseEventCallbacks}
            nvlOptions={{
              disableTelemetry: true,
              initialZoom: 1,
            }}
            onInitializationError={(e) =>
              setInitError(e instanceof Error ? e.message : String(e))
            }
            style={{ width: "100%", height: "100%", minHeight: 160 }}
          />
        )}
      </div>
    </section>
  );
}
