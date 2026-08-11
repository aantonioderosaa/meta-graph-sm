"use client";

/**
 * Query Panel — NL input, citations, subgraph highlight (tech-spec §11.3, E9).
 * Default: real POST /query (E9.3). Optional fixture via NEXT_PUBLIC_USE_QUERY_FIXTURE=true.
 */

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { ApiError, postQuery } from "@/lib/api-client";
import { QUERY_FIXTURE_RESPONSE } from "@/lib/query-fixture";
import { useAppStore } from "@/lib/store";
import type { FactUsed, QueryResponse } from "@/lib/types";

function useQueryFixtureFlag(): boolean {
  return process.env.NEXT_PUBLIC_USE_QUERY_FIXTURE === "true";
}

export function QueryPanel() {
  const setQuerySubgraph = useAppStore((s) => s.setQuerySubgraph);
  const clearHighlight = useAppStore((s) => s.clearHighlight);
  const querySubgraph = useAppStore((s) => s.querySubgraph);
  const setSelectedFactId = useAppStore((s) => s.setSelectedFactId);

  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<QueryResponse | null>(null);
  const useFixture = useQueryFixtureFlag();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const question = text.trim();
    if (!question) return;

    setLoading(true);
    setError(null);

    try {
      const result = useFixture
        ? {
            ...QUERY_FIXTURE_RESPONSE,
            answer: `${QUERY_FIXTURE_RESPONSE.answer}\n\n(Domanda: ${question})`,
          }
        : await postQuery({ text: question });

      setResponse(result);
      setQuerySubgraph(result.subgraph);
    } catch (err) {
      setResponse(null);
      clearHighlight();
      if (err instanceof ApiError) {
        setError(`Query fallita (${err.status})`);
      } else {
        setError(err instanceof Error ? err.message : "Errore sconosciuto");
      }
    } finally {
      setLoading(false);
    }
  }

  function onCitationClick(fact: FactUsed) {
    setSelectedFactId(fact.id);
  }

  return (
    <section
      aria-label="Query Panel"
      className="flex h-full min-h-[200px] flex-col rounded-lg border border-border bg-background"
    >
      <header className="flex items-center justify-between border-b border-border/60 px-3 py-2">
        <div>
          <h2 className="text-sm font-medium tracking-wide">Query Panel</h2>
          <p className="text-[10px] text-muted-foreground">
            {useFixture ? "fixture offline" : "POST /query"}
          </p>
        </div>
        {querySubgraph ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              clearHighlight();
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
                Citazioni ({response.facts_used.length})
              </h3>
              {response.facts_used.length === 0 ? (
                <p className="text-xs text-muted-foreground">Nessun fatto usato.</p>
              ) : (
                <ul className="space-y-1.5">
                  {response.facts_used.map((fact) => (
                    <li key={fact.id}>
                      <button
                        type="button"
                        className="w-full rounded border border-border/70 bg-muted/30 px-2 py-1.5 text-left text-xs hover:bg-muted/60"
                        onClick={() => onCitationClick(fact)}
                      >
                        <span className="font-mono text-[10px] text-muted-foreground">
                          [{fact.id}] · {fact.source_doc_id}
                        </span>
                        <div className="mt-0.5 text-foreground">{fact.text}</div>
                      </button>
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
