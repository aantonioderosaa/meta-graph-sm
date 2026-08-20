"use client";

/**
 * Scrollable list of every :Concept (promoted + catch-all). Not a canvas.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, getDomains } from "@/lib/api-client";
import { useAppStore } from "@/lib/store";
import type { DomainListItem } from "@/lib/types";
import { cn } from "@/lib/utils";

export function DomainDashboard({
  selectedId = null,
  onSelect,
  onLoaded,
}: {
  selectedId?: string | null;
  onSelect: (item: DomainListItem) => void;
  onLoaded?: (items: DomainListItem[]) => void;
}) {
  const kbResetEpoch = useAppStore((s) => s.kbResetEpoch);
  const lastPipelineEvent = useAppStore((s) => s.lastPipelineEvent);
  const [items, setItems] = useState<DomainListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;

  const refresh = useCallback(async () => {
    try {
      const data = await getDomains();
      setItems(data.items);
      setError(null);
      onLoadedRef.current?.(data.items);
    } catch (err) {
      setItems([]);
      onLoadedRef.current?.([]);
      if (err instanceof ApiError) {
        setError(`Domini non disponibili (${err.status})`);
      } else {
        setError(err instanceof Error ? err.message : "Errore sconosciuto");
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, kbResetEpoch]);

  useEffect(() => {
    if (
      lastPipelineEvent?.stage === "done" &&
      lastPipelineEvent.event === "pipeline_complete"
    ) {
      void refresh();
    }
  }, [lastPipelineEvent, refresh]);

  return (
    <Card
      aria-label="Dashboard domini"
      className="flex h-full min-h-[160px] flex-col overflow-hidden rounded-lg shadow-none"
    >
      <CardHeader className="space-y-0 border-b border-border/60 px-3 py-2">
        <CardTitle className="text-sm font-medium tracking-wide">Domini</CardTitle>
        <p className="text-[10px] text-muted-foreground">
          Tutti i :Concept · un clic per dizionario e regole
        </p>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-auto p-2 text-xs">
        {error ? <p className="mb-2 text-xs text-destructive">{error}</p> : null}
        {items.length === 0 && !error ? (
          <p className="p-2 text-center text-xs text-muted-foreground">
            Nessun dominio nel grafo.
          </p>
        ) : null}
        <ul className="space-y-1">
          {items.map((item) => {
            const selected = item.id === selectedId;
            return (
              <li key={item.id}>
                <button
                  type="button"
                  className={cn(
                    "w-full rounded border px-2 py-1.5 text-left hover:bg-muted/50",
                    selected
                      ? "border-primary bg-muted/60"
                      : "border-border/70 bg-muted/20",
                  )}
                  onClick={() => onSelect(item)}
                >
                  <span className="block truncate font-medium">{item.name}</span>
                  <span className="mt-0.5 flex flex-wrap gap-x-2 text-[10px] text-muted-foreground">
                    {item.kernel_category ? <span>{item.kernel_category}</span> : null}
                    <span>{item.direct_member_count} membri</span>
                    {item.promoted ? <span>promosso</span> : <span>catch-all</span>}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
