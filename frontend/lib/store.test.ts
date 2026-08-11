import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "@/lib/store";
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
    useAppStore.setState({
      nodes: [],
      relationships: [],
      selectedFactId: null,
      historyHighlight: null,
      onlyLatest: true,
      pipelineEvents: [],
      lastPipelineEvent: null,
      querySubgraph: null,
    });
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
});
