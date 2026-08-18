"use client";

/**
 * JudgeRun history (F12.5). Counts per task, not live SSE.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, getJudgeRuns } from "@/lib/api-client";
import { useAppStore } from "@/lib/store";
import type { JudgeRunItem } from "@/lib/types";

const TASKS: { key: keyof JudgeRunItem; label: string }[] = [
  { key: "anti_blur", label: "anti-blur" },
  { key: "equivalent_to", label: "equivalent_to" },
  { key: "reraffine", label: "ri-raffina" },
  { key: "identity", label: "identità" },
  { key: "missed_contradictions", label: "contraddizioni" },
  { key: "temporal", label: "temporale" },
];

export function JudgeLogPanel() {
  const kbResetEpoch = useAppStore((s) => s.kbResetEpoch);
  const [items, setItems] = useState<JudgeRunItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getJudgeRuns();
      setItems(data.items);
      setError(null);
    } catch (err) {
      setItems([]);
      if (err instanceof ApiError) {
        setError(`Log giudice non disponibile (${err.status})`);
      } else {
        setError(err instanceof Error ? err.message : "Errore sconosciuto");
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, kbResetEpoch]);

  return (
    <Card
      aria-label="Log del giudice"
      className="flex h-full min-h-[200px] flex-col overflow-hidden rounded-lg shadow-none"
    >
      <CardHeader className="space-y-0 border-b border-border/60 px-3 py-2">
        <CardTitle className="text-sm font-medium tracking-wide">Giudice</CardTitle>
        <p className="text-[10px] text-muted-foreground">
          Cronologia JudgeRun · GET /graph/judge-runs
        </p>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-auto p-3 text-xs">
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        {items.length === 0 && !error ? (
          <p className="text-center text-xs text-muted-foreground">
            Nessuna passata del giudice.
          </p>
        ) : null}
        <ul className="space-y-2">
          {items.map((run) => (
            <li
              key={run.id}
              className="rounded border border-border/70 bg-muted/30 px-2 py-1.5"
            >
              <p className="font-mono text-[10px] text-muted-foreground">
                {run.timestamp ?? run.id}
                {run.batch_id ? ` · batch ${run.batch_id}` : ""}
              </p>
              <ul className="mt-1 space-y-0.5">
                {TASKS.map((task) => (
                  <li
                    key={task.key}
                    className="flex items-center justify-between text-[10px]"
                  >
                    <span>{task.label}</span>
                    <span className="tabular-nums text-muted-foreground">
                      {Number(run[task.key] ?? 0)}
                    </span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
