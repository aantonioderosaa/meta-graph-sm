"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { eventGraphStreamUrl, fetchJobs } from "@/lib/event-graph/api";
import {
  PROCESS_STAGES,
  documentoFromJob,
  graphTouchesViewport,
  jobIsLive,
  mergeJobLists,
  mergePipelineEvents,
  stageStatus,
  upsertJob,
} from "@/lib/event-graph/live-graph";
import type {
  EventGraphJob,
  EventGraphPipelineEvent,
  PipelineStage,
} from "@/lib/event-graph/types";
import { cn } from "@/lib/utils";

const STAGE_LABELS: Record<PipelineStage, string> = {
  macro: "Macro",
  espansione: "Espansione",
  collocazione_temporale: "Collocazione temporale",
  relazioni: "Relazioni",
  riconciliazione: "Riconciliazione",
  done: "Done",
  failed: "Fallito",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "pending",
  active: "active",
  done: "done",
  failed: "failed",
  skipped: "skipped",
};

type EventPipelineMonitorProps = {
  jobId?: string | null;
  onDone?: () => void;
  onProgress?: () => void;
  onLiveChange?: (live: boolean) => void;
};

function shortJobId(jobId: string): string {
  return jobId.length <= 12 ? jobId : `${jobId.slice(0, 8)}…`;
}

function eventsOf(job: EventGraphJob | null): EventGraphPipelineEvent[] {
  return job?.events ?? [];
}

export function EventPipelineMonitor({
  jobId,
  onDone,
  onProgress,
  onLiveChange,
}: EventPipelineMonitorProps) {
  const [jobs, setJobs] = useState<EventGraphJob[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const loadJobs = useCallback(async () => {
    try {
      const payload = await fetchJobs();
      setJobs((prev) => mergeJobLists(prev, payload.jobs ?? []));
    } catch {
      /* keep the last successful snapshot */
    }
  }, []);

  useEffect(() => {
    void loadJobs();
    const timer = window.setInterval(() => {
      void loadJobs();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [loadJobs]);

  useEffect(() => {
    if (!jobId) return;
    setJobs((prev) =>
      upsertJob(prev, {
        job_id: jobId,
        status: "running",
        events: prev.find((job) => job.job_id === jobId)?.events ?? [],
      }),
    );
    setSelectedId(jobId);
  }, [jobId]);

  const selected =
    jobs.find((job) => job.job_id === selectedId) ?? jobs[0] ?? null;
  const streamJobId = selected && jobIsLive(selected) ? selected.job_id : null;

  useEffect(() => {
    if (selected && selected.job_id !== selectedId) {
      setSelectedId(selected.job_id);
    }
  }, [selected, selectedId]);

  useEffect(() => {
    onLiveChange?.(jobs.some((job) => jobIsLive(job)));
  }, [jobs, onLiveChange]);

  useEffect(() => {
    if (!streamJobId) return;
    let stopped = false;
    let source: EventSource | null = null;
    let retry: number | null = null;
    let terminal = false;

    const connect = () => {
      if (stopped || terminal) return;
      source = new EventSource(eventGraphStreamUrl(streamJobId));
      source.onmessage = (msg) => {
        try {
          const parsed = JSON.parse(msg.data) as EventGraphPipelineEvent;
          let isNew = false;
          setJobs((prev) => {
            const current = prev.find((job) => job.job_id === streamJobId);
            const previousEvents = eventsOf(current ?? null);
            const events = mergePipelineEvents(previousEvents, parsed);
            if (events === previousEvents) {
              isNew = false;
              return prev;
            }
            isNew = true;
            const status =
              parsed.stage === "failed"
                ? "failed"
                : parsed.stage === "done"
                  ? "done"
                  : "running";
            return upsertJob(prev, {
              job_id: streamJobId,
              status,
              last_stage: parsed.stage,
              last_event: parsed.event,
              ts: parsed.ts,
              payload: parsed.payload,
              events,
            });
          });
          if (!isNew) return;
          if (graphTouchesViewport(parsed)) onProgress?.();
          if (parsed.stage === "done") {
            terminal = true;
            onDone?.();
            source?.close();
          } else if (parsed.stage === "failed") {
            terminal = true;
            source?.close();
          }
        } catch {
          /* ignore malformed frames */
        }
      };
      source.onerror = () => {
        source?.close();
        if (!stopped && !terminal) {
          retry = window.setTimeout(connect, 1000);
        }
      };
    };

    connect();
    return () => {
      stopped = true;
      source?.close();
      if (retry != null) window.clearTimeout(retry);
    };
  }, [streamJobId, onDone, onProgress]);

  const events = eventsOf(selected);
  const failed = events.some((event) => event.stage === "failed");
  const failPayload = [...events].reverse().find((event) => event.stage === "failed");

  const liveHint = useMemo(() => {
    if (jobs.some((job) => jobIsLive(job))) return "Aggiornamento live";
    if (jobs.length > 0) return "Storico ingestioni";
    return null;
  }, [jobs]);

  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-sm">Pipeline</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 p-4 pt-0">
        {liveHint ? (
          <p className="text-xs text-muted-foreground">{liveHint}</p>
        ) : (
          <p className="text-xs text-muted-foreground">
            In attesa di un&apos;ingestione.
          </p>
        )}
        {jobs.length > 0 ? (
          <ul className="max-h-28 space-y-1 overflow-y-auto">
            {jobs.map((job) => {
              const active = selected?.job_id === job.job_id;
              const doc = documentoFromJob(job);
              return (
                <li key={job.job_id}>
                  <button
                    type="button"
                    className={cn(
                      "flex w-full items-center justify-between rounded border px-2 py-1 text-left text-[11px]",
                      active
                        ? "border-sky-300 bg-sky-50"
                        : "border-border text-muted-foreground hover:bg-muted/50",
                    )}
                    onClick={() => setSelectedId(job.job_id)}
                  >
                    <span className="min-w-0 truncate">
                      <code>{shortJobId(job.job_id)}</code>
                      {doc ? ` · ${doc}` : ""}
                    </span>
                    <span className="ml-2 shrink-0 uppercase tracking-wide">
                      {job.status}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
        {selected ? (
          <p className="text-xs text-muted-foreground">
            job_id: <code className="break-all">{selected.job_id}</code>
          </p>
        ) : null}
        <ol className="space-y-1.5">
          {PROCESS_STAGES.map((stage) => {
            const status = stageStatus(stage, events);
            return (
              <li
                key={stage}
                className={cn(
                  "flex items-center justify-between rounded border px-2 py-1 text-xs",
                  status === "done" && "border-emerald-300 bg-emerald-50",
                  status === "active" && "border-sky-300 bg-sky-50",
                  status === "skipped" && "border-border bg-muted/40 text-muted-foreground",
                  status === "pending" && "border-border text-muted-foreground",
                )}
              >
                <span>{STAGE_LABELS[stage]}</span>
                <span className="uppercase tracking-wide">
                  {STATUS_LABELS[status] ?? status}
                </span>
              </li>
            );
          })}
          <li
            className={cn(
              "flex items-center justify-between rounded border px-2 py-1 text-xs",
              events.some((event) => event.stage === "done")
                ? "border-emerald-300 bg-emerald-50"
                : "border-border text-muted-foreground",
            )}
          >
            <span>{STAGE_LABELS.done}</span>
            <span className="uppercase tracking-wide">
              {events.some((event) => event.stage === "done") ? "done" : "pending"}
            </span>
          </li>
        </ol>
        {failed ? (
          <p className="text-xs text-destructive" role="alert">
            Pipeline fallita
            {failPayload?.payload?.error
              ? `: ${String(failPayload.payload.error)}`
              : "."}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
