import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "@/lib/store";
import type { PipelineEvent } from "@/lib/types";

const fixtureEvent: PipelineEvent = {
  ts: "2026-01-01T00:00:00Z",
  job_id: "job-1",
  stage: "node_extraction",
  event: "participation_extracted",
  payload: { event_id: "ev-1", entity_id: "en-1", chunk_id: "c1" },
};

describe("useAppStore", () => {
  beforeEach(() => {
    useAppStore.setState({
      pipelineEvents: [],
      lastPipelineEvent: null,
      activeJobId: null,
      streamRestartToken: 0,
      kbResetEpoch: 0,
    });
  });

  it("exposes a pipeline event to any subscriber", () => {
    useAppStore.getState().pushPipelineEvent(fixtureEvent);

    const events = useAppStore.getState().pipelineEvents;
    const last = useAppStore.getState().lastPipelineEvent;

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual(fixtureEvent);
    expect(last?.event).toBe("participation_extracted");
  });

  it("notifyKnowledgeBaseReset clears pipeline state and bumps epoch", () => {
    useAppStore.getState().pushPipelineEvent(fixtureEvent);
    useAppStore.getState().setActiveJobId("job-1");

    useAppStore.getState().notifyKnowledgeBaseReset();

    const s = useAppStore.getState();
    expect(s.pipelineEvents).toEqual([]);
    expect(s.lastPipelineEvent).toBeNull();
    expect(s.activeJobId).toBeNull();
    expect(s.kbResetEpoch).toBe(1);
  });
});
