"use client";

/**
 * Unified event source for Pipeline Monitor (E8.1 / E8.4).
 * Mock when NEXT_PUBLIC_USE_MOCK_EVENTS=true; else real SSE /events/stream.
 */

import { useEffect } from "react";

import { eventsStreamUrl } from "@/lib/api-client";
import { startMockEventReplay } from "@/lib/mockEventReplayer";
import { useAppStore } from "@/lib/store";
import type { PipelineEvent } from "@/lib/types";

export function useMockEventsFlag(): boolean {
  return process.env.NEXT_PUBLIC_USE_MOCK_EVENTS === "true";
}

function isPipelineEvent(value: unknown): value is PipelineEvent {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.ts === "string" &&
    typeof v.job_id === "string" &&
    typeof v.stage === "string" &&
    typeof v.event === "string" &&
    typeof v.payload === "object" &&
    v.payload !== null
  );
}

export type UseEventStreamOptions = {
  /** Required for live SSE; ignored in mock mode. */
  jobId?: string | null;
  /** When false, do not connect / replay. Default true. */
  enabled?: boolean;
  /** Bump to restart mock replay or reconnect. */
  restartToken?: number;
};

/**
 * Subscribe to pipeline events and push them into the Zustand store.
 */
export function useEventStream(options: UseEventStreamOptions = {}): {
  mode: "mock" | "sse" | "idle";
} {
  const { jobId = null, enabled = true, restartToken = 0 } = options;
  const pushPipelineEvent = useAppStore((s) => s.pushPipelineEvent);
  const clearPipelineEvents = useAppStore((s) => s.clearPipelineEvents);
  const useMock = useMockEventsFlag();

  useEffect(() => {
    if (!enabled) return;

    if (useMock) {
      clearPipelineEvents();
      const handle = startMockEventReplay((event) => {
        pushPipelineEvent(event);
      });
      return () => {
        handle.stop();
      };
    }

    if (!jobId) return;

    clearPipelineEvents();
    const url = eventsStreamUrl(jobId);
    const source = new EventSource(url);

    source.onmessage = (msg) => {
      try {
        const parsed: unknown = JSON.parse(msg.data);
        if (isPipelineEvent(parsed)) {
          pushPipelineEvent(parsed);
          if (parsed.stage === "done" || parsed.stage === "failed") {
            source.close();
          }
        }
      } catch {
        // ignore malformed frames
      }
    };

    source.onerror = () => {
      source.close();
    };

    return () => {
      source.close();
    };
  }, [
    enabled,
    jobId,
    useMock,
    restartToken,
    pushPipelineEvent,
    clearPipelineEvents,
  ]);

  if (!enabled) return { mode: "idle" };
  if (useMock) return { mode: "mock" };
  if (jobId) return { mode: "sse" };
  return { mode: "idle" };
}
