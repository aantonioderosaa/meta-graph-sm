"use client";

/**
 * Metadata sheet for a macro node click. Facets reuse IdentityDetailPanel (F12.2).
 */

import { useCallback, useEffect, useState } from "react";

import { IdentityDetailPanel } from "@/components/IdentityDetailPanel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getNodeMetadata, userFacingApiError } from "@/lib/api-client";
import { nodeTypeLabel, shouldEmbedIdentityPanel } from "@/lib/domain-nav";
import type { NodeMetadataResponse } from "@/lib/types";

export function NodeMetadataPanel({
  nodeId,
  onHighlightChange,
}: {
  nodeId: string;
  onHighlightChange?: (ids: Set<string> | null) => void;
}) {
  const [data, setData] = useState<NodeMetadataResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await getNodeMetadata(nodeId);
      setData(next);
      setError(null);
    } catch (err) {
      setData(null);
      setError(userFacingApiError(err, "Metadati"));
    }
  }, [nodeId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const attrs = data?.attributes ?? {};
  const attrKeys = Object.keys(attrs);

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <Card
        aria-label="Metadati nodo"
        className="flex min-h-[160px] flex-col overflow-hidden rounded-lg shadow-none"
      >
        <CardHeader className="space-y-0 border-b border-border/60 px-3 py-2">
          <CardTitle className="text-sm font-medium tracking-wide">Metadati</CardTitle>
          <p className="truncate text-[10px] text-muted-foreground">
            {data?.name ?? nodeId}
          </p>
        </CardHeader>
        <CardContent className="min-h-0 flex-1 overflow-auto p-3 text-xs">
          {error ? <p className="mb-2 text-xs text-destructive">{error}</p> : null}
          {!data && !error ? (
            <p className="text-center text-xs text-muted-foreground">Caricamento…</p>
          ) : null}
          {data ? (
            <dl className="space-y-1.5 text-xs">
              {data.kernel_category ? (
                <div>
                  <dt className="text-[10px] uppercase text-muted-foreground">
                    Categoria kernel
                  </dt>
                  <dd>{data.kernel_category}</dd>
                </div>
              ) : null}
              {data.kind === "concept" ? (
                <>
                  {data.definition ? (
                    <div>
                      <dt className="text-[10px] uppercase text-muted-foreground">
                        Definizione
                      </dt>
                      <dd>{data.definition}</dd>
                    </div>
                  ) : null}
                  {(data.aliases?.length ?? 0) > 0 ? (
                    <div>
                      <dt className="text-[10px] uppercase text-muted-foreground">
                        Alias
                      </dt>
                      <dd>{data.aliases?.join(", ")}</dd>
                    </div>
                  ) : null}
                  {(data.is_a_breadcrumb?.length ?? 0) > 0 ? (
                    <div>
                      <dt className="text-[10px] uppercase text-muted-foreground">
                        Contenimento
                      </dt>
                      <dd>{data.is_a_breadcrumb?.map((item) => item.name).join(" → ")}</dd>
                    </div>
                  ) : null}
                  {data.member_count != null ? (
                    <div>
                      <dt className="text-[10px] uppercase text-muted-foreground">
                        Membri
                      </dt>
                      <dd>{data.member_count}</dd>
                    </div>
                  ) : null}
                </>
              ) : (
                <>
                  {nodeTypeLabel(data.node_type) ? (
                    <div>
                      <dt className="text-[10px] uppercase text-muted-foreground">
                        Tipo
                      </dt>
                      <dd>{nodeTypeLabel(data.node_type)}</dd>
                    </div>
                  ) : null}
                  {data.summary ? (
                    <div>
                      <dt className="text-[10px] uppercase text-muted-foreground">
                        Sommario
                      </dt>
                      <dd>{data.summary}</dd>
                    </div>
                  ) : null}
                  {attrKeys.length > 0 ? (
                    <div>
                      <dt className="text-[10px] uppercase text-muted-foreground">
                        Attributi
                      </dt>
                      <dd>
                        <ul className="mt-0.5 space-y-0.5">
                          {attrKeys.map((key) => (
                            <li key={key}>
                              <span className="font-medium">{key}:</span>{" "}
                              {String(attrs[key])}
                            </li>
                          ))}
                        </ul>
                      </dd>
                    </div>
                  ) : null}
                </>
              )}
            </dl>
          ) : null}
        </CardContent>
      </Card>
      {data && shouldEmbedIdentityPanel(data) ? (
        <IdentityDetailPanel
          filterFacetNodeId={nodeId}
          identityUris={data.identity_uris}
          onHighlightChange={onHighlightChange}
        />
      ) : null}
    </div>
  );
}
