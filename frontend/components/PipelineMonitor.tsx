"use client";

/**
 * Pipeline Monitor step view + raw log (tech-spec §11.2, E8.2).
 * Event subscription lives in PipelineEventBridge (single instance).
 */

import { useMemo, useState } from "react";

import { useAppStore } from "@/lib/store";
import type { PipelineEvent } from "@/lib/types";

// Ordine di esecuzione: chunking/node_extraction (ingestione) → entity_resolution
// → entity_relation_classification/event_resolution_and_classification (dreaming;
// questi due girano in parallelo tra loro, mostrati come righe adiacenti) →
// reconciliation → done.
const STAGES: PipelineEvent["stage"][] = [
  "chunking",
  "node_extraction",
  "entity_resolution",
  "entity_relation_classification",
  "event_resolution_and_classification",
  "reconciliation",
  "done",
];

const STAGE_LABELS: Record<PipelineEvent["stage"], string> = {
  chunking: "Chunking",
  node_extraction: "Estrazione entità/eventi",
  entity_resolution: "Risoluzione entità",
  entity_relation_classification: "Relazioni (entità)",
  event_resolution_and_classification: "Risoluzione eventi",
  reconciliation: "Riconciliazione",
  done: "Done",
  failed: "Fallito",
};

/** Ultimo evento "failed" nello stream, se presente (esito alternativo a "done"). */
function findFailure(events: PipelineEvent[]): PipelineEvent | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (events[i].stage === "failed") return events[i];
  }
  return null;
}

function stageStatus(
  stage: PipelineEvent["stage"],
  events: PipelineEvent[],
): "pending" | "active" | "done" {
  const counts = events.filter((e) => e.stage === stage).length;
  const hasDone = events.some((e) => e.stage === "done");
  const hasFailed = events.some((e) => e.stage === "failed");
  if (counts === 0) {
    if (hasDone) return stage === "done" ? "done" : "pending";
    // Un job fallito non deve mostrare l'ultimo stage raggiunto come "active"
    // per sempre — congela tutto ciò che non è già confermato "done" a "pending",
    // il banner d'errore comunica lo stato reale.
    if (hasFailed) return "pending";
    const lastStage = events[events.length - 1]?.stage;
    if (lastStage && STAGES.indexOf(stage) === STAGES.indexOf(lastStage) + 1) {
      return "active";
    }
    return "pending";
  }
  if (stage === "done") return "done";
  const stageIdx = STAGES.indexOf(stage);
  const laterExists = events.some(
    (e) => STAGES.indexOf(e.stage) > stageIdx || e.stage === "done",
  );
  return laterExists || hasDone ? "done" : "active";
}

export function PipelineMonitor() {
  const events = useAppStore((s) => s.pipelineEvents);
  const [logOpen, setLogOpen] = useState(false);

  const failure = useMemo(() => findFailure(events), [events]);

  const counts = useMemo(() => {
    const map = Object.fromEntries(STAGES.map((s) => [s, 0])) as Record<
      PipelineEvent["stage"],
      number
    >;
    for (const e of events) {
      map[e.stage] = (map[e.stage] ?? 0) + 1;
    }
    return map;
  }, [events]);

  return (
    <section
      aria-label="Pipeline Monitor"
      className="flex h-full min-h-[200px] flex-col rounded-lg border border-border bg-background"
    >
      <header className="flex items-center justify-between border-b border-border/60 px-3 py-2">
        <h2 className="text-sm font-medium tracking-wide">Pipeline Monitor</h2>
        <span className="text-xs text-muted-foreground">{events.length} eventi</span>
      </header>

      {failure ? (
        <div className="border-b border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <p className="font-medium">Pipeline fallita</p>
          <p className="mt-0.5 break-words text-destructive/90">
            {typeof failure.payload.error === "string"
              ? failure.payload.error
              : "Errore non specificato — vedi il log eventi grezzi qui sotto."}
          </p>
        </div>
      ) : null}

      <ol className="space-y-1.5 overflow-auto px-3 py-3">
        {STAGES.map((stage) => {
          const status = stageStatus(stage, events);
          return (
            <li
              key={stage}
              className="flex items-center justify-between rounded border border-border/50 px-2 py-1.5 text-xs"
            >
              <span className="flex items-center gap-2">
                <span
                  className={
                    status === "done"
                      ? "h-2 w-2 rounded-full bg-emerald-600"
                      : status === "active"
                        ? "h-2 w-2 animate-pulse rounded-full bg-amber-500"
                        : "h-2 w-2 rounded-full bg-muted-foreground/30"
                  }
                />
                {STAGE_LABELS[stage]}
              </span>
              <span className="tabular-nums text-muted-foreground">
                {counts[stage]} · {status}
              </span>
            </li>
          );
        })}
      </ol>

      <div className="mt-auto border-t border-border/60">
        <button
          type="button"
          className="flex w-full items-center justify-between px-3 py-2 text-left text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setLogOpen((o) => !o)}
        >
          <span>Log eventi grezzi</span>
          <span>{logOpen ? "▾" : "▸"}</span>
        </button>
        {logOpen ? (
          <pre className="max-h-40 overflow-auto bg-muted/40 px-3 py-2 font-mono text-[10px] leading-relaxed">
            {events.length === 0 ? "[]" : JSON.stringify(events, null, 2)}
          </pre>
        ) : null}
      </div>
    </section>
  );
}
