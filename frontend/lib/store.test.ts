import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  isPulseTimerActiveForTests,
  PULSE_DURATION_MS,
  PULSE_TICK_MS,
  resetPulseSchedulerForTests,
  useAppStore,
} from "@/lib/store";
import type { PipelineEvent } from "@/lib/types";

const fixtureEvent: PipelineEvent = {
  ts: "2026-01-01T00:00:00Z",
  job_id: "job-1",
  stage: "extraction",
  event: "fact_extracted",
  payload: { fact_id: "f1", chunk_id: "c1", type: "fact" },
};

describe("useAppStore", () => {
  beforeEach(() => {
    resetPulseSchedulerForTests();
    useAppStore.setState({
      nodes: [],
      relationships: [],
      selectedFactId: null,
      historyHighlight: null,
      onlyLatest: true,
      pulsingIds: [],
      pipelineEvents: [],
      lastPipelineEvent: null,
      activeJobId: null,
      streamRestartToken: 0,
      querySubgraph: null,
      kbResetEpoch: 0,
    });
  });

  afterEach(() => {
    resetPulseSchedulerForTests();
    vi.useRealTimers();
  });

  it("exposes a pipeline event to any subscriber without Graph Explorer", () => {
    useAppStore.getState().pushPipelineEvent(fixtureEvent);

    const events = useAppStore.getState().pipelineEvents;
    const last = useAppStore.getState().lastPipelineEvent;

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual(fixtureEvent);
    expect(last?.event).toBe("fact_extracted");
  });

  it("keeps graph and query slices independent", () => {
    useAppStore.getState().setGraph(
      [{ id: "n1", caption: "A", properties: {} }],
      [],
    );
    useAppStore.getState().setQuerySubgraph({
      nodes: [{ id: "n1", label: "Fact", properties: {} }],
      relationships: [],
    });
    useAppStore.getState().clearHighlight();

    expect(useAppStore.getState().nodes).toHaveLength(1);
    expect(useAppStore.getState().querySubgraph).toBeNull();
    expect(useAppStore.getState().pipelineEvents).toHaveLength(0);
  });

  it("notifyKnowledgeBaseReset clears graph/pipeline/query and bumps epoch", () => {
    useAppStore.getState().setGraph(
      [{ id: "n1", caption: "A", properties: {} }],
      [{ id: "r1", from: "n1", to: "n1", type: "EXTENDS" }],
    );
    useAppStore.getState().setSelectedFactId("n1");
    useAppStore.getState().setHistoryHighlight({ nodeIds: ["n1"], relIds: [] });
    useAppStore.getState().pushPipelineEvent(fixtureEvent);
    useAppStore.getState().setActiveJobId("job-1");
    useAppStore.getState().setQuerySubgraph({
      nodes: [{ id: "n1", label: "Fact", properties: {} }],
      relationships: [],
    });

    useAppStore.getState().notifyKnowledgeBaseReset();

    const s = useAppStore.getState();
    expect(s.nodes).toEqual([]);
    expect(s.relationships).toEqual([]);
    expect(s.selectedFactId).toBeNull();
    expect(s.historyHighlight).toBeNull();
    expect(s.pipelineEvents).toEqual([]);
    expect(s.lastPipelineEvent).toBeNull();
    expect(s.activeJobId).toBeNull();
    expect(s.querySubgraph).toBeNull();
    expect(s.kbResetEpoch).toBe(1);
  });

  it("batches five rapid pulseEntities into a single shared interval", () => {
    vi.useFakeTimers();
    const setIntervalSpy = vi.spyOn(globalThis, "setInterval");
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout");

    const pulse = useAppStore.getState().pulseEntities;
    pulse(["a"]);
    pulse(["b"]);
    pulse(["c"]);
    pulse(["d"]);
    pulse(["e"]);

    expect(setIntervalSpy).toHaveBeenCalledTimes(1);
    expect(setIntervalSpy).toHaveBeenCalledWith(
      expect.any(Function),
      PULSE_TICK_MS,
    );
    // F2.1: no per-call clear timeouts
    expect(
      setTimeoutSpy.mock.calls.filter(([, ms]) => ms === PULSE_DURATION_MS),
    ).toHaveLength(0);
    expect(isPulseTimerActiveForTests()).toBe(true);
    expect([...useAppStore.getState().pulsingIds].sort()).toEqual([
      "a",
      "b",
      "c",
      "d",
      "e",
    ]);

    vi.advanceTimersByTime(PULSE_DURATION_MS);
    expect(useAppStore.getState().pulsingIds).toEqual([]);
    expect(isPulseTimerActiveForTests()).toBe(false);
  });
});
