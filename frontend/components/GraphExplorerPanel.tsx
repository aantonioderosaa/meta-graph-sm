"use client";

/**
 * Graph Explorer placeholder — real NVL rendering arrives in Epic 7.
 */

import { useAppStore } from "@/lib/store";

export function GraphExplorerPanel() {
  const nodeCount = useAppStore((s) => s.nodes.length);
  const selectedFactId = useAppStore((s) => s.selectedFactId);
  const querySubgraph = useAppStore((s) => s.querySubgraph);

  return (
    <section
      aria-label="Graph Explorer"
      className="flex h-full min-h-[280px] flex-col rounded-lg border border-dashed border-border bg-muted/30"
    >
      <header className="flex items-center justify-between border-b border-border/60 px-4 py-2">
        <h2 className="text-sm font-medium tracking-wide">Graph Explorer</h2>
        <span className="text-xs text-muted-foreground">
          {nodeCount} nodi
          {selectedFactId ? ` · sel: ${selectedFactId}` : ""}
          {querySubgraph ? " · highlight attivo" : ""}
        </span>
      </header>
      <div className="flex flex-1 items-center justify-center p-6 text-center text-sm text-muted-foreground">
        Placeholder — il rendering NVL arriverà in Epic 7.
      </div>
    </section>
  );
}
