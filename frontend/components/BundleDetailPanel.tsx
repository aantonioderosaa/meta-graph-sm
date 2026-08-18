"use client";

/**
 * Expanded fascio for a macro edge. Badges reuse citation-badges (F12.6).
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, getGraphBundle } from "@/lib/api-client";
import { citationBadge, listCitationBadges } from "@/lib/citation-badges";
import type { BundleRelation } from "@/lib/types";
import { cn } from "@/lib/utils";

function provenanceText(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function BundleDetailPanel({
  nodeAId,
  nodeBId,
}: {
  nodeAId: string;
  nodeBId: string;
}) {
  const [items, setItems] = useState<BundleRelation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getGraphBundle(nodeAId, nodeBId);
      setItems(data.items);
      setError(null);
    } catch (err) {
      setItems([]);
      if (err instanceof ApiError) {
        setError(`Fascio non disponibile (${err.status})`);
      } else {
        setError(err instanceof Error ? err.message : "Errore sconosciuto");
      }
    }
  }, [nodeAId, nodeBId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const badgeSource = useMemo(
    () => ({
      citations: items.map((item) => ({
        id: item.id,
        epistemic_status:
          item.epistemic_status === "derived" ? ("derived" as const) : ("asserted" as const),
      })),
      cited_node_ids: items.map((item) => item.id),
    }),
    [items],
  );

  return (
    <Card
      aria-label="Dettaglio fascio"
      className="flex min-h-[160px] flex-col overflow-hidden rounded-lg shadow-none"
    >
      <CardHeader className="space-y-0 border-b border-border/60 px-3 py-2">
        <CardTitle className="text-sm font-medium tracking-wide">Fascio</CardTitle>
        <p className="truncate font-mono text-[10px] text-muted-foreground">
          {nodeAId} ↔ {nodeBId}
        </p>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-auto p-3 text-xs">
        {error ? <p className="mb-2 text-xs text-destructive">{error}</p> : null}
        {items.length === 0 && !error ? (
          <p className="text-center text-xs text-muted-foreground">
            Nessuna relazione asserita tra questi nodi.
          </p>
        ) : null}
        <ul className="space-y-1.5">
          {items.map((item) => {
            const badge =
              citationBadge(item.id, badgeSource) ??
              listCitationBadges(badgeSource).find((entry) => entry.id === item.id) ??
              null;
            const derived = badge?.status === "derived";
            const expanded = openId === item.id;
            return (
              <li
                key={item.id}
                className="rounded border border-border/70 bg-muted/30 px-2 py-1.5"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-medium">{item.relation ?? item.type}</span>
                  {badge ? (
                    derived ? (
                      <button
                        type="button"
                        className="rounded bg-amber-500/15 px-1 py-0.5 text-[9px] uppercase tracking-wide text-amber-700"
                        onClick={() => setOpenId(expanded ? null : item.id)}
                      >
                        {badge.label}
                      </button>
                    ) : (
                      <span className="rounded bg-muted px-1 py-0.5 text-[9px] uppercase tracking-wide text-muted-foreground">
                        {badge.label}
                      </span>
                    )
                  ) : null}
                  <button
                    type="button"
                    className="ml-auto text-[10px] text-muted-foreground underline-offset-2 hover:underline"
                    onClick={() => setOpenId(expanded ? null : item.id)}
                  >
                    {expanded ? "Nascondi" : "Dettagli"}
                  </button>
                </div>
                {expanded ? (
                  <dl className="mt-1.5 space-y-0.5 text-[10px] text-muted-foreground">
                    {item.kernel_parent ? (
                      <div>
                        <dt className="inline font-medium">kernel_parent:</dt>{" "}
                        <dd className="inline">{item.kernel_parent}</dd>
                      </div>
                    ) : null}
                    {(item.witnesses_a?.length ?? 0) > 0 ? (
                      <div>
                        <dt className="inline font-medium">testimoni A:</dt>{" "}
                        <dd className="inline">{item.witnesses_a?.join(", ")}</dd>
                      </div>
                    ) : null}
                    {(item.witnesses_b?.length ?? 0) > 0 ? (
                      <div>
                        <dt className="inline font-medium">testimoni B:</dt>{" "}
                        <dd className="inline">{item.witnesses_b?.join(", ")}</dd>
                      </div>
                    ) : null}
                    {item.valid_time ? (
                      <div>
                        <dt className="inline font-medium">valid_time:</dt>{" "}
                        <dd className="inline">{item.valid_time}</dd>
                      </div>
                    ) : null}
                    {item.system_time ? (
                      <div>
                        <dt className="inline font-medium">system_time:</dt>{" "}
                        <dd className="inline">{item.system_time}</dd>
                      </div>
                    ) : null}
                    {provenanceText(item.provenance) ? (
                      <div className={cn("break-all")}>
                        <dt className="inline font-medium">provenance:</dt>{" "}
                        <dd className="inline">{provenanceText(item.provenance)}</dd>
                      </div>
                    ) : null}
                  </dl>
                ) : null}
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
