"use client";

/**
 * NL query on the Node / Concept layer (Macrotask 6).
 * POST /graph/query — highlight is parent-owned local state, not zustand.
 */

import { Quote } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { ApiError, getNodeQueryHistory, postNodeQuery } from "@/lib/api-client";
import { citationBadge, listCitationBadges } from "@/lib/citation-badges";
import { idsFromNodeQuerySubgraph } from "@/lib/node-query-highlight";
import {
  formatQueryHistoryLabel,
  loadNodeQueryFromHistory,
} from "@/lib/node-query-history";
import { useAppStore } from "@/lib/store";
import type { NodeQueryResponse, QueryHistoryEntry } from "@/lib/types";
import { cn } from "@/lib/utils";

const NODE_TYPE_LABEL: Record<"entity" | "event", string> = {
  entity: "entità",
  event: "evento",
};

export function NodeQueryPanel({
  highlightIds,
  onHighlightChange,
}: {
  highlightIds: Set<string> | null;
  onHighlightChange: (ids: Set<string> | null) => void;
}) {
  const kbResetEpoch = useAppStore((s) => s.kbResetEpoch);

  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<NodeQueryResponse | null>(null);
  const [history, setHistory] = useState<QueryHistoryEntry[]>([]);
  const [selectedHistoryId, setSelectedHistoryId] = useState("");

  const refreshHistory = useCallback(async () => {
    try {
      const data = await getNodeQueryHistory(20);
      setHistory(data.items);
    } catch {
      // history is non-critical for submitting new queries
    }
  }, []);

  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  useEffect(() => {
    if (kbResetEpoch === 0) return;
    setHistory([]);
    setSelectedHistoryId("");
    setResponse(null);
    setError(null);
    onHighlightChange(null);
    void refreshHistory();
  }, [kbResetEpoch, refreshHistory, onHighlightChange]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const question = text.trim();
    if (!question) return;

    setLoading(true);
    setError(null);

    try {
      const result = await postNodeQuery({ text: question });
      setResponse(result);
      onHighlightChange(idsFromNodeQuerySubgraph(result.subgraph));
      setSelectedHistoryId("");
      await refreshHistory();
    } catch (err) {
      setResponse(null);
      onHighlightChange(null);
      if (err instanceof ApiError) {
        setError(`Query fallita (${err.status})`);
      } else {
        setError(err instanceof Error ? err.message : "Errore sconosciuto");
      }
    } finally {
      setLoading(false);
    }
  }

  async function onHistorySelect(id: string) {
    setSelectedHistoryId(id);
    if (!id) return;

    setLoading(true);
    setError(null);
    try {
      const result = await loadNodeQueryFromHistory(id);
      setResponse(result);
      onHighlightChange(idsFromNodeQuerySubgraph(result.subgraph));
      const entry = history.find((h) => h.id === id);
      if (entry) setText(entry.text);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`Cronologia non disponibile (${err.status})`);
      } else {
        setError(err instanceof Error ? err.message : "Errore sconosciuto");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <section
      aria-label="Query Entità/Eventi"
      className="flex h-full min-h-[200px] flex-col rounded-lg border border-border bg-background"
    >
      <header className="flex items-center justify-between border-b border-border/60 px-3 py-2">
        <div>
          <h2 className="text-sm font-medium tracking-wide">
            Query Entità/Eventi
          </h2>
          <p className="text-[10px] text-muted-foreground">POST /graph/query</p>
        </div>
        {highlightIds && highlightIds.size > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              onHighlightChange(null);
            }}
          >
            Pulisci highlight
          </Button>
        ) : null}
      </header>

      <form onSubmit={onSubmit} className="flex gap-2 border-b border-border/60 px-3 py-2">
        <input
          className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-1.5 text-sm"
          placeholder="Domanda in linguaggio naturale…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={loading}
        />
        <Button type="submit" size="sm" disabled={loading || !text.trim()}>
          {loading ? "…" : "Chiedi"}
        </Button>
      </form>

      <div className="border-b border-border/60 px-3 py-2">
        <label className="flex flex-col gap-1 text-[10px] text-muted-foreground">
          <span>Cronologia</span>
          <select
            className="rounded border border-input bg-background px-2 py-1.5 text-xs text-foreground"
            value={selectedHistoryId}
            disabled={loading || history.length === 0}
            onChange={(e) => void onHistorySelect(e.target.value)}
            aria-label="Cronologia query entità/eventi"
          >
            <option value="">
              {history.length === 0
                ? "Nessuna query salvata"
                : "Seleziona una query passata…"}
            </option>
            {history.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {formatQueryHistoryLabel(entry)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="flex flex-1 flex-col gap-3 overflow-auto p-3 text-sm">
        {error ? <p className="text-xs text-destructive">{error}</p> : null}

        {!response && !error ? (
          <p className="text-center text-xs text-muted-foreground">
            Invia una domanda per ottenere risposta e highlight sul grafo.
          </p>
        ) : null}

        {response ? (
          <>
            <div>
              <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Risposta
              </h3>
              <p className="whitespace-pre-wrap leading-relaxed">{response.answer}</p>
            </div>

            <div>
              <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Nodi ({response.nodes_used.length})
              </h3>
              {response.nodes_used.length === 0 ? (
                <p className="text-xs text-muted-foreground">Nessun nodo usato.</p>
              ) : (
                <ul className="space-y-1.5">
                  {response.nodes_used.map((node) => {
                    const cited = response.cited_node_ids.includes(node.id);
                    const badge = citationBadge(node.id, response);
                    const docs =
                      node.source_doc_ids.length > 0
                        ? node.source_doc_ids.join(", ")
                        : "—";
                    return (
                      <CitationRow
                        key={node.id}
                        cited={cited || badge !== null}
                        label={`${node.name} · ${NODE_TYPE_LABEL[node.type]} · ${docs}`}
                        badge={badge}
                      />
                    );
                  })}
                </ul>
              )}
            </div>

            <CitationExtras response={response} />

            <div>
              <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Concetti ({response.concepts_used.length})
              </h3>
              {response.concepts_used.length === 0 ? (
                <p className="text-xs text-muted-foreground">Nessun concetto usato.</p>
              ) : (
                <ul className="space-y-1.5">
                  {response.concepts_used.map((concept) => (
                    <li
                      key={concept.id}
                      className="rounded border border-border/70 bg-muted/30 px-2 py-1.5 text-xs"
                    >
                      {concept.name}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}

function CitationRow({
  cited,
  label,
  badge,
}: {
  cited: boolean;
  label: string;
  badge: ReturnType<typeof citationBadge>;
}) {
  const [open, setOpen] = useState(false);
  const derived = badge?.status === "derived";
  return (
    <li
      className={cn(
        "w-full rounded border bg-muted/30 px-2 py-1.5 text-left text-xs",
        cited ? "border-primary" : "border-border/70",
      )}
    >
      <span className="flex flex-wrap items-center gap-1.5 font-mono text-[10px] text-muted-foreground">
        {cited ? (
          <Quote className="h-3 w-3 shrink-0 text-primary" aria-label="Citato nella risposta" />
        ) : null}
        {label}
        {badge ? (
          derived ? (
            <button
              type="button"
              className="rounded bg-amber-500/15 px-1 py-0.5 font-sans text-[9px] uppercase tracking-wide text-amber-700"
              onClick={() => setOpen((value) => !value)}
            >
              {badge.label}
            </button>
          ) : (
            <span className="rounded bg-muted px-1 py-0.5 font-sans text-[9px] uppercase tracking-wide text-muted-foreground">
              {badge.label}
            </span>
          )
        ) : null}
      </span>
      {open && derived && badge && badge.chain.length > 0 ? (
        <ol className="mt-1 space-y-0.5 border-l border-amber-500/40 pl-2 text-[10px] text-muted-foreground">
          {badge.chain.map((step, index) => (
            <li key={`${step.kind}-${index}`}>
              <span className="font-medium uppercase">{step.kind}</span> · {step.detail}
            </li>
          ))}
        </ol>
      ) : null}
    </li>
  );
}

function CitationExtras({ response }: { response: NodeQueryResponse }) {
  const used = new Set(response.nodes_used.map((node) => node.id));
  const extras = listCitationBadges(response).filter((badge) => !used.has(badge.id));
  if (extras.length === 0) return null;
  return (
    <div>
      <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Altre citazioni ({extras.length})
      </h3>
      <ul className="space-y-1.5">
        {extras.map((badge) => (
          <CitationRow key={badge.id} cited label={badge.id} badge={badge} />
        ))}
      </ul>
    </div>
  );
}
