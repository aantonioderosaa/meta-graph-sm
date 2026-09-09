"use client";

import { useEffect, useState } from "react";

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { eventGraphStreamUrl } from "@/lib/event-graph/api";
import {
  PIPELINE_STAGES,
  type EventGraphPipelineEvent,
  type PipelineStage,
} from "@/lib/event-graph/types";
import { cn } from "@/lib/utils";

const STAGE_LABELS: Record<PipelineStage, string> = {
  estrazione: "Estrazione",
  regole_chunk: "Regole chunk",
  riconciliazione: "Riconciliazione",
  collocazione_temporale: "Collocazione temporale",
  done: "Done",
  failed: "Fallito",
};

type EventPipelineMonitorProps = {
  jobId?: string | null;
  onDone?: () => void;
};

function stageOrder(stage: string): number {
  return PIPELINE_STAGES.indexOf(stage as PipelineStage);
}

function stageStatus(
  stage: PipelineStage,
  events: EventGraphPipelineEvent[],
): "pending" | "active" | "done" | "failed" {
  const hasFailed = events.some((e) => e.stage === "failed");
  const hasDone = events.some((e) => e.stage === "done");
  if (stage === "failed") return hasFailed ? "failed" : "pending";
  if (stage === "done") return hasDone ? "done" : "pending";
  if (events.some((e) => e.stage === stage) && (hasDone || hasFailed)) {
    return hasFailed && !hasDone ? "done" : "done";
  }
  const seen = events.filter((e) => e.stage === stage);
  if (seen.length === 0) {
    const last = events[events.length - 1]?.stage;
    if (last && stageOrder(stage) === stageOrder(last) + 1 && !hasDone && !hasFailed) {
      return "active";
    }
    return "pending";
  }
  const later = events.some(
    (e) => stageOrder(e.stage) > stageOrder(stage) || e.stage === "done",
  );
  return later || hasDone ? "done" : "active";
}

export function EventPipelineMonitor({ jobId, onDone }: EventPipelineMonitorProps) {
  const [events, setEvents] = useState<EventGraphPipelineEvent[]>([]);

  useEffect(() => {
    if (!jobId) {
      setEvents([]);
      return;
    }
    setEvents([]);
    const url = eventGraphStreamUrl(jobId);
    const source = new EventSource(url);

    source.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data) as EventGraphPipelineEvent;
        setEvents((prev) => [...prev, parsed]);
        if (parsed.stage === "done") {
          onDone?.();
          source.close();
        } else if (parsed.stage === "failed") {
          source.close();
        }
      } catch {
        // ignore malformed frames
      }
    };
    source.onerror = () => {
      source.close();
    };
    return () => {
      source.close();
    };
  }, [jobId, onDone]);

  const visibleStages = PIPELINE_STAGES.filter((s) => s !== "failed");
  const failed = events.some((e) => e.stage === "failed");

  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle className="text-sm">Pipeline</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 p-4 pt-0">
        {!jobId ? (
          <p className="text-xs text-muted-foreground">In attesa di un job_id.</p>
        ) : (
          <p className="text-xs text-muted-foreground">
            job_id: <code>{jobId}</code>
          </p>
        )}
        <ol className="space-y-1.5">
          {visibleStages.map((stage) => {
            const status = stageStatus(stage, events);
            return (
              <li
                key={stage}
                className={cn(
                  "flex items-center justify-between rounded border px-2 py-1 text-xs",
                  status === "done" && "border-emerald-300 bg-emerald-50",
                  status === "active" && "border-sky-300 bg-sky-50",
                  status === "pending" && "border-border text-muted-foreground",
                )}
              >
                <span>{STAGE_LABELS[stage]}</span>
                <span className="uppercase tracking-wide">{status}</span>
              </li>
            );
          })}
        </ol>
        {failed ? (
          <p className="text-xs text-destructive" role="alert">
            Pipeline fallita.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
