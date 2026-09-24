import { describe, expect, it } from "vitest";

import { PIPELINE_STAGES } from "./types";
import {
  LIVE_GRAPH_BATCH_SIZE,
  chunkArray,
  diffIds,
  documentoFromJob,
  graphFingerprint,
  graphTouchesViewport,
  gridPosition,
  jobIsLive,
  mergeJobLists,
  mergePipelineEvents,
  shouldUseHeavyLayout,
  sortNodesParentsFirst,
  stageStatus,
  upsertJob,
} from "./live-graph";

describe("event-graph pipeline stages", () => {
  it("lists the live ingest processes in execution order", () => {
    expect(PIPELINE_STAGES).toEqual([
      "macro",
      "espansione",
      "collocazione_temporale",
      "relazioni",
      "riconciliazione",
      "done",
      "failed",
    ]);
    expect(PIPELINE_STAGES).not.toContain("regole_chunk");
    expect(PIPELINE_STAGES).not.toContain("estrazione");
  });

  it("marks unseen stages skipped once a later stage has run", () => {
    expect(
      stageStatus("collocazione_temporale", [
        { stage: "macro", event: "macro_done" },
        { stage: "espansione", event: "zona_expanded" },
        { stage: "relazioni", event: "relazioni_done" },
      ]),
    ).toBe("skipped");
    expect(stageStatus("macro", [])).toBe("active");
    expect(
      stageStatus("espansione", [
        { stage: "macro", event: "macro_done" },
        { stage: "espansione", event: "zona_start" },
      ]),
    ).toBe("active");
    expect(
      stageStatus("macro", [
        { stage: "macro", event: "macro_done" },
        { stage: "done", event: "pipeline_complete" },
      ]),
    ).toBe("done");
  });
});

describe("live graph helpers", () => {
  it("chunks work into small batches", () => {
    expect(chunkArray([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
    expect(LIVE_GRAPH_BATCH_SIZE).toBeLessThanOrEqual(48);
  });

  it("diffs ids without dropping the remainder", () => {
    expect(diffIds(["a", "b"], ["b", "c", "d"])).toEqual({
      add: ["c", "d"],
      remove: ["a"],
    });
  });

  it("places every index on a stable grid", () => {
    const seen = new Set<string>();
    for (let i = 0; i < 200; i += 1) {
      const pos = gridPosition(i);
      seen.add(`${pos.x},${pos.y}`);
    }
    expect(seen.size).toBe(200);
  });

  it("inserts compound parents before children", () => {
    const sorted = sortNodesParentsFirst([
      { data: { id: "child", label: "c", tipo: "Evento", parent: "zona" } },
      { data: { id: "zona", label: "z", tipo: "Zona" } },
    ]);
    expect(sorted.map((node) => node.data.id)).toEqual(["zona", "child"]);
  });

  it("skips heavy layout while live or above the size cap", () => {
    expect(shouldUseHeavyLayout(40, true)).toBe(false);
    expect(shouldUseHeavyLayout(40, false)).toBe(true);
    expect(shouldUseHeavyLayout(5000, false)).toBe(false);
  });

  it("fingerprints graph payloads for no-op polls", () => {
    const a = {
      nodes: [{ data: { id: "n1", label: "a", tipo: "Evento" } }],
      edges: [{ data: { id: "e1", source: "n1", target: "n1", tipo: "SEQUENZA" } }],
    };
    const b = {
      nodes: [{ data: { id: "n1", label: "a", tipo: "Evento" } }],
      edges: [{ data: { id: "e1", source: "n1", target: "n1", tipo: "CAUSA" } }],
    };
    expect(graphFingerprint(a)).not.toBe(graphFingerprint(b));
    expect(graphFingerprint(a)).toBe(
      graphFingerprint({
        nodes: [...a.nodes],
        edges: [...a.edges],
      }),
    );
  });
});

describe("pipeline jobs", () => {
  it("merges remote jobs without dropping the optimistic running one", () => {
    const local = [
      { job_id: "new", status: "running", events: [] },
      { job_id: "old", status: "done", ts: "2026-01-01", events: [] },
    ];
    const remote = [
      {
        job_id: "old",
        status: "done",
        ts: "2026-01-01",
        documento: "doc-1",
        events: [{ stage: "done", event: "pipeline_complete" }],
      },
    ];
    const merged = mergeJobLists(local, remote);
    expect(merged.map((job) => job.job_id)).toEqual(["new", "old"]);
    expect(documentoFromJob(merged[1])).toBe("doc-1");
    expect(jobIsLive(merged[0])).toBe(true);
  });

  it("keeps local SSE events when a remote poll still has an empty history", () => {
    const local = [
      {
        job_id: "new",
        status: "running",
        events: [{ stage: "macro", event: "macro_start" }],
      },
    ];
    const remote = [{ job_id: "new", status: "running", events: [] }];
    const merged = mergeJobLists(local, remote);
    expect(merged[0].events).toHaveLength(1);
    expect(merged[0].status).toBe("running");
  });

  it("upserts a job to the top of the list", () => {
    const next = upsertJob(
      [{ job_id: "a", status: "done" }],
      { job_id: "b", status: "running" },
    );
    expect(next[0].job_id).toBe("b");
  });

  it("dedupes replayed SSE events", () => {
    const event = {
      ts: "t",
      job_id: "j",
      stage: "macro",
      event: "macro_done",
      payload: { n: 1 },
    };
    const once = mergePipelineEvents([], event);
    const twice = mergePipelineEvents(once, event);
    expect(twice).toHaveLength(1);
  });

  it("treats persist events as graph touches", () => {
    expect(
      graphTouchesViewport({ stage: "espansione", event: "zona_extracted" }),
    ).toBe(true);
    expect(
      graphTouchesViewport({ stage: "macro", event: "macro_start" }),
    ).toBe(true);
  });
});
