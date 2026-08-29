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
    ts: "2026-01-01T12:00:01.000Z",
    job_id: "mock-job-1",
    stage: "node_extraction",
    event: "participation_extracted",
    payload: { event_id: "ev-1", entity_id: "en-1", chunk_id: "chunk-1" },
  },
  {
    ts: "2026-01-01T12:00:02.000Z",
    job_id: "mock-job-1",
    stage: "entity_resolution",
    event: "node_merged",
    payload: { dup_id: "en-2", canon_id: "en-1" },
  },
  {
    ts: "2026-01-01T12:00:02.250Z",
    job_id: "mock-job-1",
    stage: "backbone_classification",
    event: "backbone_member_assigned",
    payload: { node_id: "en-1", concept_id: "c-agente", kernel_category: "Agente" },
  },
  {
    ts: "2026-01-01T12:00:02.400Z",
    job_id: "mock-job-1",
    stage: "promote_clusters",
    event: "cluster_promoted",
    payload: { concept_id: "c-promoted", parent_id: "c-agente", member_count: 5 },
  },
  {
    ts: "2026-01-01T12:00:02.500Z",
    job_id: "mock-job-1",
    stage: "entity_relation_classification",
    event: "node_relation_classified",
    payload: { type: "extends", src: "en-1", tgt: "en-3" },
  },
  {
    ts: "2026-01-01T12:00:03.000Z",
    job_id: "mock-job-1",
    stage: "event_resolution_and_classification",
    event: "node_relation_classified",
    payload: { type: "precedes", src: "ev-1", tgt: "ev-2" },
  },
  {
    ts: "2026-01-01T12:00:03.800Z",
    job_id: "mock-job-1",
    stage: "reconciliation",
    event: "drift_check",
    payload: { drift_count: 0 },
  },
  {
    ts: "2026-01-01T12:00:03.900Z",
    job_id: "mock-job-1",
    stage: "judge",
    event: "judge_complete",
    payload: {
      stats: {
        anti_blur: 0,
        equivalent_to: 0,
        reraffine: 0,
        temporal: 0,
      },
    },
  },
  {
    ts: "2026-01-01T12:00:04.000Z",
    job_id: "mock-job-1",
    stage: "done",
    event: "pipeline_complete",
    payload: { stats: { node_drift_count: 0 } },
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
