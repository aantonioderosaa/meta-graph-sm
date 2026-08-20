"use client";

/**
 * Side card (not a modal): Σ_D dictionary, scoped S1 rules, direct children.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, getDomainChildrenGraph, getDomainDictionary, getDomainRules } from "@/lib/api-client";
import { partitionDomainChildren } from "@/lib/domain-nav";
import type {
  ConnectivityRuleItem,
  DomainDictionaryItem,
  DomainListItem,
  GraphNode,
} from "@/lib/types";

export function DomainDetailCard({
  domain,
  onSelectConcept,
  onSelectNode,
}: {
  domain: DomainListItem;
  onSelectConcept: (id: string) => void;
  onSelectNode: (id: string, nodeType?: string | null) => void;
}) {
  const [dictionary, setDictionary] = useState<DomainDictionaryItem[]>([]);
  const [rules, setRules] = useState<ConnectivityRuleItem[]>([]);
  const [concepts, setConcepts] = useState<GraphNode[]>([]);
  const [members, setMembers] = useState<GraphNode[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [dict, scopedRules, children] = await Promise.all([
        getDomainDictionary(domain.id),
        getDomainRules(domain.id),
        getDomainChildrenGraph(domain.id),
      ]);
      setDictionary(dict.items);
      setRules(scopedRules.items);
      const partitioned = partitionDomainChildren(children.nodes);
      setConcepts(partitioned.concepts);
      setMembers(partitioned.members);
      setError(null);
    } catch (err) {
      setDictionary([]);
      setRules([]);
      setConcepts([]);
      setMembers([]);
      if (err instanceof ApiError) {
        setError(`Dettaglio dominio non disponibile (${err.status})`);
      } else {
        setError(err instanceof Error ? err.message : "Errore sconosciuto");
      }
    }
  }, [domain.id]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const relations = dictionary.filter((item) => item.kind === "relation");
  const attributes = dictionary.filter((item) => item.kind === "attribute");

  return (
    <Card
      aria-label="Scheda dominio"
      className="flex h-full min-h-[160px] flex-col overflow-hidden rounded-lg shadow-none"
    >
      <CardHeader className="space-y-0 border-b border-border/60 px-3 py-2">
        <CardTitle className="truncate text-sm font-medium tracking-wide">
          {domain.name}
        </CardTitle>
        <p className="text-[10px] text-muted-foreground">
          {domain.kernel_category ?? "senza categoria"}
          {domain.promoted ? " · promosso" : " · catch-all"}
          {` · ${domain.direct_member_count} membri`}
        </p>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-auto p-3 text-xs">
        {error ? <p className="mb-2 text-xs text-destructive">{error}</p> : null}
        {domain.definition ? (
          <p className="mb-3 text-[11px] text-muted-foreground">{domain.definition}</p>
        ) : null}

        <section className="mb-3">
          <h3 className="mb-1 text-[10px] font-medium uppercase text-muted-foreground">
            Dizionario Σ_D
          </h3>
          {relations.length === 0 && attributes.length === 0 ? (
            <p className="text-muted-foreground">Nessun fatto in questo dominio.</p>
          ) : (
            <ul className="space-y-1">
              {relations.map((item) => (
                <li
                  key={`rel:${item.name}:${item.kernel_parent ?? ""}`}
                  className="rounded border border-border/70 bg-muted/30 px-2 py-1"
                >
                  <span className="font-medium">{item.name}</span>
                  <span className="ml-1 text-[10px] text-muted-foreground">
                    relazione · {item.count}
                    {item.kernel_parent ? ` · ${item.kernel_parent}` : ""}
                  </span>
                </li>
              ))}
              {attributes.map((item) => (
                <li
                  key={`attr:${item.name}`}
                  className="rounded border border-border/70 bg-muted/30 px-2 py-1"
                >
                  <span className="font-medium">{item.name}</span>
                  <span className="ml-1 text-[10px] text-muted-foreground">
                    attributo · {item.count}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="mb-3">
          <h3 className="mb-1 text-[10px] font-medium uppercase text-muted-foreground">
            Regole S1
          </h3>
          {rules.length === 0 ? (
            <p className="text-muted-foreground">Nessuna regola per questo dominio.</p>
          ) : (
            <ul className="space-y-1">
              {rules.map((rule, index) => (
                <li
                  key={`${rule.source_category}|${rule.relation_type}|${rule.target_category}|${index}`}
                  className="rounded border border-border/70 bg-muted/30 px-2 py-1"
                >
                  {rule.source_category}{" "}
                  <span className="text-muted-foreground">—{rule.relation_type}→</span>{" "}
                  {rule.target_category}
                  <span className="ml-1 text-[10px] text-muted-foreground">
                    liv. {rule.generalization_level} · origini {rule.origin_count}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section>
          <h3 className="mb-1 text-[10px] font-medium uppercase text-muted-foreground">
            Figli diretti
          </h3>
          {concepts.length === 0 && members.length === 0 ? (
            <p className="text-muted-foreground">Nessun figlio diretto.</p>
          ) : (
            <ul className="space-y-1">
              {concepts.map((child) => (
                <li key={child.id}>
                  <button
                    type="button"
                    className="w-full rounded px-1 py-1 text-left hover:bg-muted/50"
                    onClick={() => onSelectConcept(child.id)}
                  >
                    <span className="font-medium">{child.caption}</span>
                    <span className="ml-1 text-[10px] text-muted-foreground">
                      Concept
                    </span>
                  </button>
                </li>
              ))}
              {members.map((child) => {
                const nodeType = String(child.properties?.type ?? "node");
                return (
                  <li key={child.id}>
                    <button
                      type="button"
                      className="w-full rounded px-1 py-1 text-left hover:bg-muted/50"
                      onClick={() => onSelectNode(child.id, nodeType)}
                    >
                      <span className="font-medium">{child.caption}</span>
                      <span className="ml-1 text-[10px] text-muted-foreground">
                        {nodeType === "event" ? "Evento" : "Entità"}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </CardContent>
    </Card>
  );
}
