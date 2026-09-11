"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ingestDocument, resetGraph } from "@/lib/event-graph/api";

type EventIngestPanelProps = {
  onJobStarted?: (jobId: string) => void;
  onGraphReset?: () => void;
};

export function EventIngestPanel({ onJobStarted, onGraphReset }: EventIngestPanelProps) {
  const [docId, setDocId] = useState("doc-1");
  const [text, setText] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [confirmingReset, setConfirmingReset] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const [resetResult, setResetResult] = useState<number | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!docId.trim() || !text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await ingestDocument(docId.trim(), text);
      setJobId(result.job_id);
      onJobStarted?.(result.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ingestione fallita");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmReset() {
    setResetting(true);
    setResetError(null);
    try {
      const result = await resetGraph();
      setResetResult(result.rimossi);
      setConfirmingReset(false);
      onGraphReset?.();
    } catch (err) {
      setResetError(err instanceof Error ? err.message : "Pulizia del grafo fallita");
    } finally {
      setResetting(false);
    }
  }

  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-sm">Ingestione</CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-xs">
            <span>doc_id</span>
            <input
              className="rounded border border-input bg-background px-2 py-1.5 text-sm"
              value={docId}
              onChange={(e) => setDocId(e.target.value)}
              name="doc_id"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span>testo</span>
            <textarea
              className="min-h-[6rem] rounded border border-input bg-background px-2 py-1.5 text-sm"
              value={text}
              onChange={(e) => setText(e.target.value)}
              name="text"
              placeholder="Incolla il testo da ingerire…"
            />
          </label>
          <Button type="submit" size="sm" disabled={busy || !text.trim() || !docId.trim()}>
            {busy ? "Invio…" : "Ingerisci"}
          </Button>
          {jobId ? (
            <p className="text-xs text-foreground" data-testid="job-id">
              job_id: <code className="rounded bg-muted px-1">{jobId}</code>
            </p>
          ) : null}
          {error ? (
            <p className="text-xs text-destructive" role="alert">
              {error}
            </p>
          ) : null}
        </form>

        <div className="mt-4 flex flex-col gap-2 border-t border-border pt-3">
          {!confirmingReset ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="text-destructive"
              onClick={() => {
                setConfirmingReset(true);
                setResetError(null);
                setResetResult(null);
              }}
            >
              Pulisci grafo
            </Button>
          ) : (
            <div className="flex flex-col gap-2 text-xs">
              <p className="text-destructive">
                Elimina TUTTI i documenti, eventi, zone e cluster dal grafo. L&apos;operazione
                non è reversibile. Confermi?
              </p>
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="destructive"
                  disabled={resetting}
                  onClick={() => void onConfirmReset()}
                >
                  {resetting ? "Pulizia…" : "Conferma pulizia"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={resetting}
                  onClick={() => setConfirmingReset(false)}
                >
                  Annulla
                </Button>
              </div>
            </div>
          )}
          {resetResult != null ? (
            <p className="text-xs text-foreground" data-testid="reset-result">
              Grafo pulito: {resetResult} nodi rimossi.
            </p>
          ) : null}
          {resetError ? (
            <p className="text-xs text-destructive" role="alert">
              {resetError}
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
