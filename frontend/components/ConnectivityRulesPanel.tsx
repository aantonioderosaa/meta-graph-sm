"use client";

/**
 * Debug list of S1 ConnectivityRule rows (F12.4).
 */

import { useCallback, useEffect, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, getConnectivityRules } from "@/lib/api-client";
import { useAppStore } from "@/lib/store";
import type { ConnectivityRuleItem } from "@/lib/types";

export function ConnectivityRulesPanel() {
  const kbResetEpoch = useAppStore((s) => s.kbResetEpoch);
  const [items, setItems] = useState<ConnectivityRuleItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getConnectivityRules();
      setItems(data.items);
      setError(null);
    } catch (err) {
      setItems([]);
      if (err instanceof ApiError) {
        setError(`Regole S1 non disponibili (${err.status})`);
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
      aria-label="Regole di connettività"
      className="flex h-full min-h-[200px] flex-col overflow-hidden rounded-lg shadow-none"
    >
      <CardHeader className="space-y-0 border-b border-border/60 px-3 py-2">
        <CardTitle className="text-sm font-medium tracking-wide">Regole S1</CardTitle>
        <p className="text-[10px] text-muted-foreground">
          ConnectivityRule · origini fattuali
        </p>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-auto p-3 text-xs">
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        {items.length === 0 && !error ? (
          <p className="text-center text-xs text-muted-foreground">
            Nessuna regola di connettività.
          </p>
        ) : null}
        <ul className="space-y-1.5">
          {items.map((rule, index) => (
            <li
              key={`${rule.source_category}|${rule.relation_type}|${rule.target_category}|${index}`}
              className="rounded border border-border/70 bg-muted/30 px-2 py-1.5"
            >
              <p className="font-medium">
                {rule.source_category}{" "}
                <span className="text-muted-foreground">—{rule.relation_type}→</span>{" "}
                {rule.target_category}
              </p>
              <p className="text-[10px] text-muted-foreground">
                livello {rule.generalization_level} · origini {rule.origin_count}
              </p>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
