"use client";

/**
 * Pipeline controls residual after F3: mock/SSE mode indicator, manual job_id
 * override, Reset/Replay. Ingest form lives on /documents (F3.3).
 */

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { useMockEventsFlag } from "@/lib/useEventStream";
import { useAppStore } from "@/lib/store";

export function PipelineEventBridge() {
  const useMock = useMockEventsFlag();
  const activeJobId = useAppStore((s) => s.activeJobId);
  const setActiveJobId = useAppStore((s) => s.setActiveJobId);
  const clearPipelineEvents = useAppStore((s) => s.clearPipelineEvents);
  const bumpStreamRestart = useAppStore((s) => s.bumpStreamRestart);

  const mode = useMock ? "mock" : activeJobId ? "sse" : "idle";

  return (
    <div className="space-y-2 border-b border-border/40 bg-muted/20 px-3 py-2 text-[10px] text-muted-foreground">
      <div className="flex flex-wrap items-center gap-2" data-pipeline-mode={mode}>
        <span>
          Stream: <strong className="text-foreground">{mode}</strong>
          {useMock ? " (mock offline)" : null}
        </span>
        {!useMock ? (
          <>
            <input
              className="min-w-[10rem] flex-1 rounded border border-input bg-background px-2 py-0.5 text-[11px] text-foreground"
              placeholder="job_id SSE"
              value={activeJobId ?? ""}
              onChange={(e) => setActiveJobId(e.target.value.trim() || null)}
            />
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[10px]"
              onClick={() => {
                clearPipelineEvents();
                bumpStreamRestart();
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
                bumpStreamRestart();
              }}
            >
              Replay mock
            </Button>
            <span className="text-[10px] italic">
              gli eventi mostrati sono fittizi — ingest/dream su{" "}
              <Link href="/documents" className="underline text-foreground">
                /documents
              </Link>
              ; imposta NEXT_PUBLIC_USE_MOCK_EVENTS=false per eventi reali
            </span>
          </>
        )}
      </div>
    </div>
  );
}
