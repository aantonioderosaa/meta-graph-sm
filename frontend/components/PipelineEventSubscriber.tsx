"use client";

/**
 * Global SSE/mock subscription — mounted once in layout so Pipeline Monitor
 * keeps receiving events across route changes (F3.2).
 */

import { useEventStream, useMockEventsFlag } from "@/lib/useEventStream";
import { useAppStore } from "@/lib/store";

export function PipelineEventSubscriber() {
  const useMock = useMockEventsFlag();
  const activeJobId = useAppStore((s) => s.activeJobId);
  const streamRestartToken = useAppStore((s) => s.streamRestartToken);

  useEventStream({
    jobId: useMock ? null : activeJobId,
    enabled: useMock || Boolean(activeJobId),
    restartToken: streamRestartToken,
  });

  return null;
}
