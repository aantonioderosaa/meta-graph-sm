"use client";

/**
 * Single mount point for pipeline event subscription (avoids duplicate
 * EventSource / mock replays when the monitor is rendered in multiple places).
 */

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useEventStream, useMockEventsFlag } from "@/lib/useEventStream";
import { useAppStore } from "@/lib/store";

export function PipelineEventBridge() {
  const useMock = useMockEventsFlag();
  const [jobId, setJobId] = useState("");
  const [restartToken, setRestartToken] = useState(0);
  const clearPipelineEvents = useAppStore((s) => s.clearPipelineEvents);

  const { mode } = useEventStream({
    jobId: useMock ? null : jobId || null,
    enabled: useMock || Boolean(jobId),
    restartToken,
  });

  return (
    <div
      className="flex flex-wrap items-center gap-2 border-b border-border/40 bg-muted/20 px-3 py-1.5 text-[10px] text-muted-foreground"
      data-pipeline-mode={mode}
    >
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
      )}
    </div>
  );
}
