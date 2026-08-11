"use client";

/**
 * Pipeline Monitor placeholder — live SSE arrives in Epic 8.
 */

import { useAppStore } from "@/lib/store";

export function PipelineMonitorPanel() {
  const events = useAppStore((s) => s.pipelineEvents);
  const last = useAppStore((s) => s.lastPipelineEvent);

  return (
    <section
      aria-label="Pipeline Monitor"
      className="flex h-full min-h-[200px] flex-col rounded-lg border border-border bg-background"
    >
      <header className="flex items-center justify-between border-b border-border/60 px-4 py-2">
        <h2 className="text-sm font-medium tracking-wide">Pipeline Monitor</h2>
        <span className="text-xs text-muted-foreground">{events.length} eventi</span>
      </header>
      <div className="flex flex-1 flex-col gap-2 overflow-auto p-4 text-sm text-muted-foreground">
        {last ? (
          <p>
            Ultimo: <code className="text-foreground">{last.stage}/{last.event}</code>
          </p>
        ) : (
          <p>Nessun evento — lo stream SSE arriverà in Epic 8.</p>
        )}
      </div>
    </section>
  );
}
