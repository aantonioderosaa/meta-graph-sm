"use client";

/**
 * Identity facets list + non-destructive detach (F12.2). No merge, no node delete.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  getIdentities,
  getIdentity,
  postUnlinkFacet,
  userFacingApiError,
} from "@/lib/api-client";
import { useAppStore } from "@/lib/store";
import type { IdentityItem } from "@/lib/types";

type IdentityDetailPanelProps = {
  onHighlightChange?: (ids: Set<string> | null) => void;
  filterFacetNodeId?: string;
  /** When set, fetch only these URIs — do not list the whole KB. */
  identityUris?: string[];
};

export function IdentityDetailPanel(props: IdentityDetailPanelProps) {
  const { onHighlightChange, filterFacetNodeId, identityUris } = props;
  const kbResetEpoch = useAppStore((s) => s.kbResetEpoch);
  const [items, setItems] = useState<IdentityItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const identityKey = identityUris ? identityUris.join("\0") : null;

  const refresh = useCallback(async () => {
    try {
      if (identityUris && identityUris.length === 0) {
        setItems([]);
        setError(null);
        return;
      }
      if (identityUris) {
        const next = await Promise.all(identityUris.map((uri) => getIdentity(uri)));
        setItems(next);
      } else {
        const data = await getIdentities();
        setItems(data.items);
      }
      setError(null);
    } catch (err) {
      setItems([]);
      setError(userFacingApiError(err, "Identità"));
    }
  }, [identityKey, identityUris]);

  useEffect(() => {
    void refresh();
  }, [refresh, kbResetEpoch]);

  const onUnlink = useCallback(
    async (uri: string, facetNodeId: string) => {
      const ok = window.confirm(
        "Staccare questa faccetta dall'identità? Il nodo non viene eliminato.",
      );
      if (!ok) {
        return;
      }
      setBusy(uri + ":" + facetNodeId);
      try {
        await postUnlinkFacet(uri, facetNodeId);
        await refresh();
      } catch (err) {
        setError(userFacingApiError(err, "Stacco faccetta"));
      } finally {
        setBusy(null);
      }
    },
    [refresh],
  );

  const visibleItems = useMemo(() => {
    if (!filterFacetNodeId) {
      return items;
    }
    return items.filter((identity) =>
      identity.facets.some((facet) => facet.id === filterFacetNodeId),
    );
  }, [filterFacetNodeId, items]);

  return (
    <Card
      aria-label="Identità e faccette"
      className="flex h-full min-h-[200px] flex-col overflow-hidden rounded-lg shadow-none"
    >
      <CardHeader className="space-y-0 border-b border-border/60 px-3 py-2">
        <CardTitle className="text-sm font-medium tracking-wide">Identità</CardTitle>
        <p className="text-[10px] text-muted-foreground">
          Stacca faccetta: toglie SAME_AS, non cancella il nodo
        </p>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-auto p-3 text-xs">
        {error ? <p className="mb-2 text-xs text-destructive">{error}</p> : null}
        {visibleItems.length === 0 && !error ? (
          <p className="text-center text-xs text-muted-foreground">
            Nessuna identità con faccette.
          </p>
        ) : null}
        <ul className="space-y-2">
          {visibleItems.map((identity) => (
            <li
              key={identity.uri}
              className="rounded border border-border/70 bg-muted/30 px-2 py-1.5"
            >
              <p className="font-mono text-[10px] text-muted-foreground">
                {identity.uri}
              </p>
              <p className="mb-1 text-[10px] text-muted-foreground">
                {identity.facets.length} faccette
              </p>
              <ul className="space-y-1">
                {identity.facets.map((facet) => (
                  <li
                    key={facet.id}
                    className="flex items-center justify-between gap-2 rounded border border-border/50 bg-background px-2 py-1"
                  >
                    <button
                      type="button"
                      className="min-w-0 truncate text-left"
                      onClick={() => onHighlightChange?.(new Set([facet.id]))}
                    >
                      {facet.name}
                      {facet.kernel_category ? (
                        <span className="ml-1 text-[10px] text-muted-foreground">
                          {facet.kernel_category}
                        </span>
                      ) : null}
                    </button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-6 shrink-0 px-2 text-[10px]"
                      disabled={busy === uriKey(identity.uri, facet.id)}
                      onClick={() => void onUnlink(identity.uri, facet.id)}
                    >
                      Stacca faccetta
                    </Button>
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

function uriKey(uri: string, facetId: string): string {
  return uri + ":" + facetId;
}
