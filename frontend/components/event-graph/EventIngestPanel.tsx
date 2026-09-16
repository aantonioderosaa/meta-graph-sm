"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ingestDocument } from "@/lib/event-graph/api";

type EventIngestPanelProps = {
  onJobStarted?: (jobId: string) => void;
};

export function EventIngestPanel({ onJobStarted }: EventIngestPanelProps) {
  const [docId, setDocId] = useState("doc-1");
  const [text, setText] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      </CardContent>
    </Card>
  );
}
