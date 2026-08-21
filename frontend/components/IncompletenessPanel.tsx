"use client";

/**
 * Incomplete EventTriageRun list (Macrotask 7). Read-only GET.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, getEventIncompleteness } from "@/lib/api-client";
import { useAppStore } from "@/lib/store";
import type { EventIncompletenessItem } from "@/lib/types";

export const INCOMPLETENESS_EMPTY_COPY = "Nessun evento incompleto.";

export function IncompletenessPanel({
  onHighlightChange,
}: {
  onHighlightChange?: (ids: Set<string> | null) => void;
} = {}) {
  const kbResetEpoch = useAppStore((s) => s.kbResetEpoch);
  const [items, setItems] = useState<EventIncompletenessItem[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getEventIncompleteness();
      setItems(data.items);
      setError(null);
    } catch (err) {
      setItems([]);
      if (err instanceof ApiError) {
        setError(`Incompletezze non disponibili (${err.status})`);
      } else {
        setError(err instanceof Error ? err.message : "Errore sconosciuto");
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, kbResetEpoch]);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => {
      const blob = [
        item.event_id,
        item.text,
        item.missing_context ?? "",
        item.first_seen_run_id ?? "",
      ]
        .join(" ")
        .toLowerCase();
      return blob.includes(q);
    });
  }, [items, filter]);

  return (
    <Card
      aria-label="Visualizza incompletezze"
      className="flex h-full min-h-[200px] flex-col overflow-hidden rounded-lg shadow-none"
    >
      <CardHeader className="space-y-0 border-b border-border/60 px-3 py-2">
        <CardTitle className="text-sm font-medium tracking-wide">
          Incompletezze
        </CardTitle>
        <p className="text-[10px] text-muted-foreground">
          Eventi con verdetto incomplete — GET /graph/event-incompleteness
        </p>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden p-3 text-xs">
        <input
          className="rounded border border-input bg-background px-2 py-1 text-xs"
          placeholder="Filtra per evento o contesto…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filtra incompletezze"
        />
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        <ul className="min-h-0 flex-1 space-y-1.5 overflow-auto">
          {visible.length === 0 && !error ? (
            <li className="text-center text-xs text-muted-foreground">
              {items.length === 0
                ? INCOMPLETENESS_EMPTY_COPY
                : "Nessun risultato per il filtro (le incompletezze restano nel grafo)."}
            </li>
          ) : null}
          {visible.map((item) => (
            <li key={item.event_id}>
              <button
                type="button"
                className="w-full rounded border border-border/70 bg-muted/30 px-2 py-1.5 text-left hover:bg-muted/50"
                onClick={() => onHighlightChange?.(new Set([item.event_id]))}
              >
                <span className="font-medium">{item.text || item.event_id}</span>
                {item.missing_context ? (
                  <span className="mt-0.5 block text-[10px] text-muted-foreground">
                    manca: {item.missing_context}
                  </span>
                ) : null}
                <span className="mt-0.5 block text-[10px] text-muted-foreground">
                  {item.incomplete_at ?? item.timestamp ?? item.event_id}
                  {item.first_seen_run_id
                    ? ` · prima vista ${item.first_seen_run_id}`
                    : ""}
                  {` · ${item.checks_without_progress} controlli`}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
