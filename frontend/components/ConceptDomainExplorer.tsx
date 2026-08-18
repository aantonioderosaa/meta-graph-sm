"use client";

/**
 * Navigable IS_A / MEMBER_OF containment tree (F12.1). List/tree only — no NVL.
 */

import { ChevronRight } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, getConceptNeighbors, getConceptOverview } from "@/lib/api-client";
import {
  buildConceptTree,
  mergeConceptNeighbors,
  type ConceptTreeNode,
} from "@/lib/concept-tree";
import { useAppStore } from "@/lib/store";
import { cn } from "@/lib/utils";

function TreeRow({
  node,
  depth,
  expanded,
  loadingId,
  onToggle,
  onSelect,
}: {
  node: ConceptTreeNode;
  depth: number;
  expanded: Set<string>;
  loadingId: string | null;
  onToggle: (id: string) => void;
  onSelect?: (id: string) => void;
}) {
  const isOpen = expanded.has(node.id);
  const hasKids = node.children.length > 0 || node.members.length > 0;
  return (
    <li>
      <button
        type="button"
        className="flex w-full items-start gap-1 rounded px-1 py-1 text-left text-xs hover:bg-muted/50"
        style={{ paddingLeft: 4 + depth * 12 }}
        onClick={() => {
          onSelect?.(node.id);
          void onToggle(node.id);
        }}
      >
        <ChevronRight
          className={cn(
            "mt-0.5 h-3 w-3 shrink-0 text-muted-foreground transition-transform",
            isOpen && "rotate-90",
          )}
        />
        <span className="min-w-0">
          <span className="font-medium">{node.caption}</span>
          {node.kernel_category ? (
            <span className="ml-1 text-[10px] text-muted-foreground">
              {node.kernel_category}
            </span>
          ) : null}
          {node.definition ? (
            <span className="mt-0.5 block text-[10px] text-muted-foreground">
              {node.definition}
            </span>
          ) : null}
          {loadingId === node.id ? (
            <span className="ml-1 text-[10px] text-muted-foreground">…</span>
          ) : null}
        </span>
      </button>
      {isOpen && hasKids ? (
        <ul className="space-y-0.5">
          {node.children.map((child) => (
            <TreeRow
              key={child.id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              loadingId={loadingId}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
          {node.members.map((member) => (
            <li
              key={member.id}
              className="px-1 py-0.5 text-[10px] text-muted-foreground"
              style={{ paddingLeft: 20 + (depth + 1) * 12 }}
            >
              {member.caption}
              {member.type ? ` · ${member.type}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function ConceptDomainExplorer({
  onHighlightChange,
}: {
  onHighlightChange?: (ids: Set<string> | null) => void;
} = {}) {
  const kbResetEpoch = useAppStore((s) => s.kbResetEpoch);
  const [roots, setRoots] = useState<ConceptTreeNode[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadOverview = useCallback(async () => {
    try {
      const graph = await getConceptOverview();
      setRoots(buildConceptTree(graph));
      setError(null);
    } catch (err) {
      setRoots([]);
      if (err instanceof ApiError) {
        setError(`Albero dominio non disponibile (${err.status})`);
      } else {
        setError(err instanceof Error ? err.message : "Errore sconosciuto");
      }
    }
  }, []);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview, kbResetEpoch]);

  async function onToggle(id: string) {
    const next = new Set(expanded);
    if (next.has(id)) {
      next.delete(id);
      setExpanded(next);
      return;
    }
    next.add(id);
    setExpanded(next);
    setLoadingId(id);
    try {
      const neighbors = await getConceptNeighbors(id);
      setRoots((prev) => mergeConceptNeighbors(prev, id, neighbors));
      onHighlightChange?.(new Set([id]));
    } catch {
      // tree still shows overview nodes
    } finally {
      setLoadingId(null);
    }
  }

  return (
    <Card
      aria-label="Esploratore dominio"
      className="flex h-full min-h-[200px] flex-col overflow-hidden rounded-lg shadow-none"
    >
      <CardHeader className="space-y-0 border-b border-border/60 px-3 py-2">
        <CardTitle className="text-sm font-medium tracking-wide">Dominio</CardTitle>
        <p className="text-[10px] text-muted-foreground">IS_A / MEMBER_OF</p>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-auto p-2 text-xs">
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        {!error && roots.length === 0 ? (
          <p className="p-2 text-center text-xs text-muted-foreground">
            Nessun concetto nel grafo.
          </p>
        ) : null}
        <ul className="space-y-0.5">
          {roots.map((node) => (
            <TreeRow
              key={node.id}
              node={node}
              depth={0}
              expanded={expanded}
              loadingId={loadingId}
              onToggle={onToggle}
              onSelect={(id) => onHighlightChange?.(new Set([id]))}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
