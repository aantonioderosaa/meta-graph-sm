import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MOCK_PIPELINE_EVENTS,
  startMockEventReplay,
} from "./mockEventReplayer";
import type { PipelineEvent } from "./types";

describe("mockEventReplayer", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("replays the full §10 fixture covering every stage", async () => {
    vi.useFakeTimers();
    const received: PipelineEvent[] = [];
    const handle = startMockEventReplay((e) => received.push(e), MOCK_PIPELINE_EVENTS, 100);

    await vi.runAllTimersAsync();
    handle.stop();

    expect(received).toHaveLength(MOCK_PIPELINE_EVENTS.length);
    const stages = new Set(received.map((e) => e.stage));
    expect(stages).toEqual(
      new Set([
        "chunking",
        "node_extraction",
        "entity_resolution",
        "backbone_classification",
        "entity_relation_classification",
        "event_resolution_and_classification",
        "reconciliation",
        "done",
      ]),
    );
    expect(received[0]).toMatchObject({
      ts: expect.any(String),
      job_id: expect.any(String),
      stage: expect.any(String),
      event: expect.any(String),
      payload: expect.any(Object),
    });
    expect(received.at(-1)?.stage).toBe("done");
  });

  it("stops early without leaking timers", () => {
    vi.useFakeTimers();
    const received: PipelineEvent[] = [];
    const handle = startMockEventReplay((e) => received.push(e), MOCK_PIPELINE_EVENTS, 100);
    vi.advanceTimersByTime(50);
    handle.stop();
    vi.advanceTimersByTime(5000);
    expect(received.length).toBeLessThan(MOCK_PIPELINE_EVENTS.length);
  });
});
