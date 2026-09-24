/**
 * Pipeline job helpers + lightweight live-graph diffs.
 * Pure functions — no cytoscape, safe for unit tests.
 */

import { PIPELINE_STAGES, type EventGraphPipelineEvent, type PipelineStage } from "./types";
import type {
  EventGraphElements,
  EventGraphJob,
  EventGraphNodeElement,
} from "./types";

export const LIVE_GRAPH_BATCH_SIZE = 40;
export const LIVE_GRAPH_LAYOUT_MAX_NODES = 1200;
export const LIVE_GRAPH_POLL_MS = 2000;
export const LIVE_GRAPH_REFRESH_DEBOUNCE_MS = 450;

export const PROCESS_STAGES: PipelineStage[] = PIPELINE_STAGES.filter(
  (stage) => stage !== "done" && stage !== "failed",
);

export const GRAPH_TOUCH_EVENTS = new Set([
  "macro_done",
  "zona_extracted",
  "zona_expanded",
  "buchi_done",
  "ancore_estrazione",
  "ancore_linea",
  "ancore_persistenza",
  "relazioni_done",
  "reconcile_done",
  "pipeline_complete",
]);

export function stageOrder(stage: string): number {
  return PIPELINE_STAGES.indexOf(stage as PipelineStage);
}

export function pipelineEventKey(event: EventGraphPipelineEvent): string {
  return [
    event.ts ?? "",
    event.job_id ?? "",
    event.stage,
    event.event ?? "",
    JSON.stringify(event.payload ?? null),
  ].join("|");
}

export function mergePipelineEvents(
  prev: EventGraphPipelineEvent[],
  next: EventGraphPipelineEvent,
): EventGraphPipelineEvent[] {
  const key = pipelineEventKey(next);
  if (prev.some((event) => pipelineEventKey(event) === key)) return prev;
  return [...prev, next];
}

export function jobIsLive(job: Pick<EventGraphJob, "status"> | null | undefined): boolean {
  if (!job) return false;
  return job.status !== "done" && job.status !== "failed";
}

export function documentoFromJob(job: EventGraphJob | null | undefined): string {
  if (!job) return "";
  if (job.documento) return String(job.documento);
  const payload = job.payload ?? {};
  const fromPayload = payload.doc_id ?? payload.documento;
  if (fromPayload != null && String(fromPayload) !== "") return String(fromPayload);
  for (const event of [...(job.events ?? [])].reverse()) {
    const nested = event.payload ?? {};
    const value = nested.doc_id ?? nested.documento;
    if (value != null && String(value) !== "") return String(value);
  }
  return "";
}

export function upsertJob(jobs: EventGraphJob[], incoming: EventGraphJob): EventGraphJob[] {
  const index = jobs.findIndex((job) => job.job_id === incoming.job_id);
  if (index < 0) return [incoming, ...jobs];
  const current = jobs[index];
  const currentEvents = current.events ?? [];
  const incomingEvents = incoming.events;
  const events =
    incomingEvents != null && incomingEvents.length >= currentEvents.length
      ? incomingEvents
      : currentEvents;
  const status =
    current.status === "done" || current.status === "failed"
      ? current.status
      : (incoming.status ?? current.status);
  const merged: EventGraphJob = {
    ...current,
    ...incoming,
    events,
    status,
    payload: incoming.payload ?? current.payload,
  };
  const next = [...jobs];
  next.splice(index, 1);
  return [merged, ...next];
}

export function mergeJobLists(
  local: EventGraphJob[],
  remote: EventGraphJob[],
): EventGraphJob[] {
  let out = [...local];
  for (const job of remote) {
    out = upsertJob(out, job);
  }
  const rank = (job: EventGraphJob) => (jobIsLive(job) ? 0 : 1);
  return out.sort((a, b) => {
    const live = rank(a) - rank(b);
    if (live !== 0) return live;
    return String(b.ts ?? "").localeCompare(String(a.ts ?? ""));
  });
}

export function stageStatus(
  stage: PipelineStage,
  events: EventGraphPipelineEvent[],
): "pending" | "active" | "done" | "failed" | "skipped" {
  const hasFailed = events.some((event) => event.stage === "failed");
  const hasDone = events.some((event) => event.stage === "done");
  if (stage === "failed") return hasFailed ? "failed" : "pending";
  if (stage === "done") return hasDone ? "done" : "pending";
  const seen = events.filter((event) => event.stage === stage);
  const later = events.some(
    (event) =>
      event.stage === "done" ||
      event.stage === "failed" ||
      (stageOrder(event.stage) > stageOrder(stage) && stageOrder(event.stage) >= 0),
  );
  if (seen.length === 0) {
    if (hasFailed) return "pending";
    if (later || hasDone) return "skipped";
    if (events.length === 0 && stageOrder(stage) === 0) return "active";
    const last = events[events.length - 1]?.stage;
    if (last && stageOrder(stage) === stageOrder(last) + 1 && !hasDone && !hasFailed) {
      return "active";
    }
    return "pending";
  }
  if (later || hasDone || hasFailed) return "done";
  return "active";
}

export function graphTouchesViewport(event: EventGraphPipelineEvent): boolean {
  if (event.event && GRAPH_TOUCH_EVENTS.has(event.event)) return true;
  return (
    event.stage === "espansione" ||
    event.stage === "macro" ||
    event.stage === "relazioni" ||
    event.stage === "riconciliazione" ||
    event.stage === "collocazione_temporale" ||
    event.stage === "done"
  );
}

export function graphFingerprint(
  elements: EventGraphElements | null | undefined,
): string {
  if (!elements) return "0";
  const nodes = elements.nodes.map((node) => node.data.id).join("\n");
  const edges = elements.edges
    .map((edge) => `${edge.data.id}:${edge.data.tipo ?? ""}`)
    .join("\n");
  return `${elements.nodes.length}:${elements.edges.length}\n${nodes}\n#\n${edges}`;
}

export function diffIds(
  prev: Iterable<string>,
  next: Iterable<string>,
): { add: string[]; remove: string[] } {
  const previous = new Set(prev);
  const wanted = new Set(next);
  const add: string[] = [];
  const remove: string[] = [];
  for (const id of wanted) {
    if (!previous.has(id)) add.push(id);
  }
  for (const id of previous) {
    if (!wanted.has(id)) remove.push(id);
  }
  return { add, remove };
}

export function chunkArray<T>(items: T[], size = LIVE_GRAPH_BATCH_SIZE): T[][] {
  const out: T[][] = [];
  const n = Math.max(1, size);
  for (let i = 0; i < items.length; i += n) {
    out.push(items.slice(i, i + n));
  }
  return out;
}

export function gridPosition(
  index: number,
  gapX = 88,
  gapY = 64,
  columns = 24,
): { x: number; y: number } {
  const cols = Math.max(1, columns);
  return { x: (index % cols) * gapX, y: Math.floor(index / cols) * gapY };
}

export function sortNodesParentsFirst<T extends EventGraphNodeElement>(
  nodes: T[],
): T[] {
  const byId = new Map(nodes.map((node) => [node.data.id, node]));
  const rankOf = (node: T): number => {
    let rank = 0;
    let parent = node.data.parent;
    const seen = new Set<string>();
    while (parent && byId.has(parent) && !seen.has(parent)) {
      seen.add(parent);
      rank += 1;
      parent = byId.get(parent)?.data.parent ?? null;
    }
    return rank;
  };
  return [...nodes].sort((left, right) => {
    const delta = rankOf(left) - rankOf(right);
    if (delta !== 0) return delta;
    return left.data.id.localeCompare(right.data.id);
  });
}

export function sortNodesChildrenFirst<T extends EventGraphNodeElement>(
  nodes: T[],
): T[] {
  return sortNodesParentsFirst(nodes).reverse();
}

export function shouldUseHeavyLayout(nodeCount: number, live: boolean): boolean {
  if (live) return false;
  return nodeCount > 0 && nodeCount <= LIVE_GRAPH_LAYOUT_MAX_NODES;
}
