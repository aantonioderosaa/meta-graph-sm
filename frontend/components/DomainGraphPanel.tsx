"use client";

/**
 * Successor of MacroGraphPanel: nested-scope NVL via GraphPanel.
 * Root = Concepts with BUNDLE edges only. Drill = direct children of a domain.
 */

import { ArrowLeft } from "lucide-react";
import { useCallback, useState } from "react";

import { GraphPanel } from "@/components/GraphPanel";
import { Button } from "@/components/ui/button";
import { getDomainChildrenGraph, getDomainsGraph } from "@/lib/api-client";
import { currentDrillId, partitionDomainChildren } from "@/lib/domain-nav";
import { colorByKernelCategory } from "@/lib/graph-encoding";
import type { GraphNode, GraphRelationship } from "@/lib/types";

export function DomainGraphPanel({
  drillPath,
  onBack,
  onNodeClick,
  onRelationshipClick,
  selectedId = null,
}: {
  drillPath: string[];
  onBack: () => void;
  onNodeClick?: (id: string, node: GraphNode) => void;
  onRelationshipClick?: (rel: GraphRelationship) => void;
  selectedId?: string | null;
}) {
  const scopeId = currentDrillId(drillPath);
  const [members, setMembers] = useState<GraphNode[]>([]);

  const fetcher = useCallback(async () => {
    const data = scopeId
      ? await getDomainChildrenGraph(scopeId)
      : await getDomainsGraph();
    setMembers(scopeId ? partitionDomainChildren(data.nodes).members : []);
    return data;
  }, [scopeId]);

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-1 overflow-hidden">
      {scopeId ? (
        <div className="flex shrink-0 items-center gap-2 px-1">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onBack}
            aria-label="Torna indietro"
          >
            <ArrowLeft className="mr-1 h-3.5 w-3.5" />
            Indietro
          </Button>
          <span className="truncate text-[10px] text-muted-foreground">
            Scope · {scopeId}
          </span>
        </div>
      ) : null}
      <div
        className={
          scopeId
            ? "grid min-h-0 flex-1 grid-cols-1 gap-2 overflow-hidden lg:grid-cols-[minmax(0,1fr)_minmax(10rem,14rem)]"
            : "flex min-h-0 flex-1 flex-col overflow-hidden"
        }
      >
        <GraphPanel
          key={scopeId ?? "root"}
          title={scopeId ? "Sottodominio" : "Domini collegati"}
          fetcher={fetcher}
          reloadKey={scopeId ?? "root"}
          colorForNode={colorByKernelCategory}
          onNodeClick={onNodeClick}
          onRelationshipClick={onRelationshipClick}
          selectedId={selectedId}
          emptyMessage={
            scopeId
              ? "Nessun figlio diretto in questo dominio."
              : "Nessun fascio tra domini. I catch-all isolati restano nella lista sopra."
          }
        />
        {scopeId ? (
          <aside
            aria-label="Membri MEMBER_OF"
            className="min-h-0 overflow-auto rounded-lg border border-border bg-background p-2 text-xs"
          >
            <p className="mb-1 text-[10px] font-medium uppercase text-muted-foreground">
              Dentro questo dominio
            </p>
            {members.length === 0 ? (
              <p className="text-muted-foreground">Nessun :Node MEMBER_OF.</p>
            ) : (
              <ul className="space-y-1">
                {members.map((node) => {
                  const nodeType = String(node.properties?.type ?? "");
                  return (
                    <li key={node.id}>
                      <button
                        type="button"
                        className="w-full rounded px-1 py-0.5 text-left hover:bg-muted/50"
                        onClick={() => onNodeClick?.(node.id, node)}
                      >
                        <span className="font-medium">{node.caption}</span>
                        <span className="ml-1 text-[10px] text-muted-foreground">
                          {nodeType === "event" ? "evento" : "entità"}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </aside>
        ) : null}
      </div>
    </div>
  );
}
