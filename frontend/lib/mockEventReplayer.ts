/**
 * Mock pipeline event sequence (tech-spec §10) for offline demo (E8.1).
 * Field-for-field identical to live SSE payloads.
 */

import type { PipelineEvent } from "./types";

export const MOCK_PIPELINE_EVENTS: PipelineEvent[] = [
  {
    ts: "2026-01-01T12:00:00.000Z",
    job_id: "mock-job-1",
    stage: "chunking",
    event: "chunk_created",
    payload: { chunk_id: "chunk-1", doc_id: "doc-mock-1" },
  },
  {
    ts: "2026-01-01T12:00:00.300Z",
    job_id: "mock-job-1",
    stage: "chunking",
    event: "chunk_created",
    payload: { chunk_id: "chunk-2", doc_id: "doc-mock-1" },
  },
  {
    ts: "2026-01-01T12:00:01.000Z",
    job_id: "mock-job-1",
    stage: "extraction",
    event: "fact_extracted",
    payload: { fact_id: "fact-a", chunk_id: "chunk-1", type: "fact" },
  },
  {
    ts: "2026-01-01T12:00:01.400Z",
    job_id: "mock-job-1",
    stage: "extraction",
    event: "chunk_discarded_noise",
    payload: { chunk_id: "chunk-2" },
  },
  {
    ts: "2026-01-01T12:00:02.000Z",
    job_id: "mock-job-1",
    stage: "grouping",
    event: "group_formed",
    payload: { component_id: 1, fact_ids: ["fact-a", "fact-b"] },
  },
  {
    ts: "2026-01-01T12:00:02.500Z",
    job_id: "mock-job-1",
    stage: "consolidation",
    event: "fact_derived",
    payload: { fact_id: "fact-d", source_fact_ids: ["fact-a", "fact-b"] },
  },
  {
    ts: "2026-01-01T12:00:03.000Z",
    job_id: "mock-job-1",
    stage: "relation_detection",
    event: "edge_created",
    payload: { type: "extends", src: "fact-a", tgt: "fact-b" },
  },
  {
    ts: "2026-01-01T12:00:03.300Z",
    job_id: "mock-job-1",
    stage: "relation_detection",
    event: "is_latest_changed",
    payload: { fact_id: "fact-b", value: false },
  },
  {
    ts: "2026-01-01T12:00:03.800Z",
    job_id: "mock-job-1",
    stage: "reconciliation",
    event: "drift_check",
    payload: { drift_count: 0 },
  },
  {
    ts: "2026-01-01T12:00:04.000Z",
    job_id: "mock-job-1",
    stage: "done",
    event: "pipeline_complete",
    payload: { stats: { chunks: 2, facts: 2, edges: 1 } },
  },
];

export type MockReplayHandle = {
  stop: () => void;
};

/**
 * Publish fixture events with 200–500ms artificial delay.
 * Calls `onEvent` for each item; stops on `stage === "done"` after that event.
 */
export function startMockEventReplay(
  onEvent: (event: PipelineEvent) => void,
  events: PipelineEvent[] = MOCK_PIPELINE_EVENTS,
  delayMs = 300,
): MockReplayHandle {
  let cancelled = false;
  let index = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const tick = () => {
    if (cancelled || index >= events.length) return;
    const event = events[index];
    index += 1;
    onEvent(event);
    if (event.stage === "done" || index >= events.length) return;
    const jitter = 200 + Math.floor(Math.random() * 300);
    timer = setTimeout(tick, delayMs > 0 ? delayMs : jitter);
  };

  timer = setTimeout(tick, 50);

  return {
    stop: () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    },
  };
}
