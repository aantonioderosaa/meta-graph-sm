"use client";

/**
 * Query Panel placeholder — NL query UI arrives in Epic 9.
 */

import { useAppStore } from "@/lib/store";
import { Button } from "@/components/ui/button";

export function QueryPanel() {
  const querySubgraph = useAppStore((s) => s.querySubgraph);
  const clearHighlight = useAppStore((s) => s.clearHighlight);

  return (
    <section
      aria-label="Query Panel"
      className="flex h-full min-h-[200px] flex-col rounded-lg border border-border bg-background"
    >
      <header className="flex items-center justify-between border-b border-border/60 px-4 py-2">
        <h2 className="text-sm font-medium tracking-wide">Query Panel</h2>
        {querySubgraph ? (
          <Button variant="ghost" size="sm" onClick={clearHighlight}>
            Pulisci highlight
          </Button>
        ) : null}
      </header>
      <div className="flex flex-1 items-center justify-center p-4 text-center text-sm text-muted-foreground">
        Placeholder — input NL e citazioni arriveranno in Epic 9.
      </div>
    </section>
  );
}
