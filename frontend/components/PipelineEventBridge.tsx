"use client";

/**
 * Single mount point for pipeline event subscription + document ingest (E8/E10).
 */

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { postDocuments, postDreamingRun } from "@/lib/api-client";
import { useEventStream, useMockEventsFlag } from "@/lib/useEventStream";
import { useAppStore } from "@/lib/store";

export function PipelineEventBridge() {
  const useMock = useMockEventsFlag();
  const [jobId, setJobId] = useState("");
  const [restartToken, setRestartToken] = useState(0);
  const [docId, setDocId] = useState("doc-1");
  const [docText, setDocText] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const clearPipelineEvents = useAppStore((s) => s.clearPipelineEvents);

  const { mode } = useEventStream({
    jobId: useMock ? null : jobId || null,
    enabled: useMock || Boolean(jobId),
    restartToken,
  });

  async function onIngest() {
    if (!docText.trim()) return;
    setBusy(true);
    setStatus(null);
    try {
      const { job_id } = await postDocuments({
        doc_id: docId || "doc-1",
        text: docText,
      });
      setJobId(job_id);
      setRestartToken((t) => t + 1);
      setStatus(`Ingest avviato · job_id=${job_id}`);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Ingest fallito");
    } finally {
      setBusy(false);
    }
  }

  async function onDream() {
    setBusy(true);
    setStatus(null);
    try {
      const { job_id } = await postDreamingRun({});
      setJobId(job_id);
      setRestartToken((t) => t + 1);
      setStatus(`Dreaming avviato · job_id=${job_id}`);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Dreaming fallito");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 border-b border-border/40 bg-muted/20 px-3 py-2 text-[10px] text-muted-foreground">
      <div className="flex flex-wrap items-center gap-2" data-pipeline-mode={mode}>
        <span>
          Eventi: <strong className="text-foreground">{mode}</strong>
          {useMock ? " (mock offline)" : null}
        </span>
        {!useMock ? (
          <>
            <input
              className="min-w-[10rem] flex-1 rounded border border-input bg-background px-2 py-0.5 text-[11px] text-foreground"
              placeholder="job_id SSE"
              value={jobId}
              onChange={(e) => setJobId(e.target.value.trim())}
            />
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[10px]"
              onClick={() => {
                clearPipelineEvents();
                setRestartToken((t) => t + 1);
              }}
            >
              Reset
            </Button>
          </>
        ) : (
          <>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[10px]"
              onClick={() => {
                clearPipelineEvents();
                setRestartToken((t) => t + 1);
              }}
            >
              Replay mock
            </Button>
            <span className="text-[10px] italic">
              gli eventi mostrati sono fittizi, non provengono da questo ingest — imposta
              NEXT_PUBLIC_USE_MOCK_EVENTS=false per vederli reali
            </span>
          </>
        )}
      </div>

      {/* L'ingest chiama sempre il backend reale: è indipendente dal flag mock,
          che riguarda solo la sorgente degli eventi mostrati nel Pipeline Monitor. */}
      <div className="flex flex-col gap-1.5 border-t border-border/30 pt-2 md:flex-row md:items-end">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-foreground/70 md:self-center">
          Ingestione
        </span>
        <label className="flex min-w-[8rem] flex-col gap-0.5">
          <span>doc_id</span>
          <input
            className="rounded border border-input bg-background px-2 py-1 text-[11px] text-foreground"
            value={docId}
            onChange={(e) => setDocId(e.target.value)}
          />
        </label>
        <label className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span>testo documento</span>
          <textarea
            className="min-h-[2.5rem] rounded border border-input bg-background px-2 py-1 text-[11px] text-foreground"
            value={docText}
            onChange={(e) => setDocText(e.target.value)}
            placeholder="Incolla un paragrafo da ingerire…"
          />
        </label>
        <div className="flex gap-1">
          <Button
            size="sm"
            className="h-7 text-[10px]"
            disabled={busy || !docText.trim()}
            onClick={() => void onIngest()}
          >
            Ingest
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-[10px]"
            disabled={busy}
            onClick={() => void onDream()}
          >
            Dream
          </Button>
        </div>
      </div>

      {status ? <p className="text-[10px] text-foreground">{status}</p> : null}
    </div>
  );
}
