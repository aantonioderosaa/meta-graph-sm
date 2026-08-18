"use client";

/**
 * Open CONTRADICTS list — never auto-hidden (F12.3).
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, getContradictions } from "@/lib/api-client";
import { useAppStore } from "@/lib/store";
import type { ContradictionItem } from "@/lib/types";

export function ContradictionsPanel({
  onHighlightChange,
}: {
  onHighlightChange?: (ids: Set<string> | null) => void;
} = {}) {
  const kbResetEpoch = useAppStore((s) => s.kbResetEpoch);
  const [items, setItems] = useState<ContradictionItem[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getContradictions();
      setItems(data.items);
      setError(null);
    } catch (err) {
      setItems([]);
      if (err instanceof ApiError) {
        setError(`Contraddizioni non disponibili (${err.status})`);
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
        item.left_name,
        item.right_name,
        item.left_id,
        item.right_id,
        item.subject_id ?? "",
      ]
        .join(" ")
        .toLowerCase();
      return blob.includes(q);
    });
  }, [items, filter]);

  return (
    <Card
      aria-label="Contraddizioni aperte"
      className="flex h-full min-h-[200px] flex-col overflow-hidden rounded-lg shadow-none"
    >
      <CardHeader className="space-y-0 border-b border-border/60 px-3 py-2">
        <CardTitle className="text-sm font-medium tracking-wide">
          Contraddizioni
        </CardTitle>
        <p className="text-[10px] text-muted-foreground">
          CONTRADICTS aperti — mai nascosti automaticamente
        </p>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden p-3 text-xs">
        <input
          className="rounded border border-input bg-background px-2 py-1 text-xs"
          placeholder="Filtra per nome o id…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filtra contraddizioni"
        />
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        <ul className="min-h-0 flex-1 space-y-1.5 overflow-auto">
          {visible.length === 0 && !error ? (
            <li className="text-center text-xs text-muted-foreground">
              {items.length === 0
                ? "Nessuna contraddizione aperta."
                : "Nessun risultato per il filtro (le contraddizioni restano nel grafo)."}
            </li>
          ) : null}
          {visible.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className="w-full rounded border border-border/70 bg-muted/30 px-2 py-1.5 text-left hover:bg-muted/50"
                onClick={() =>
                  onHighlightChange?.(new Set([item.left_id, item.right_id]))
                }
              >
                <span className="font-medium">{item.left_name}</span>
                <span className="mx-1 text-muted-foreground">↔</span>
                <span className="font-medium">{item.right_name}</span>
                {item.subject_id ? (
                  <span className="mt-0.5 block text-[10px] text-muted-foreground">
                    soggetto {item.subject_id}
                  </span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
