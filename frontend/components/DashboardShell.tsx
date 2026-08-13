"use client";

/**
 * Dashboard shell: single EntityEventExplorer mount (always visible) plus
 * Pipeline Monitor / Node Query around it. Desktop sidebar at lg; mobile uses
 * Sheets — never a second graph (WebGL context cap).
 */

import { useState } from "react";
import { Activity, MessageSquareText } from "lucide-react";

import { EntityEventExplorer } from "@/components/EntityEventExplorer";
import { NodeQueryPanel } from "@/components/NodeQueryPanel";
import { PipelineEventBridge } from "@/components/PipelineEventBridge";
import { PipelineMonitorPanel } from "@/components/PipelineMonitorPanel";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

export function DashboardShell() {
  const [pipelineOpen, setPipelineOpen] = useState(false);
  const [queryOpen, setQueryOpen] = useState(false);
  const [highlightIds, setHighlightIds] = useState<Set<string> | null>(null);

  return (
    <div className="flex min-h-screen flex-col overflow-x-hidden bg-background md:h-[calc(100dvh-2.75rem)] md:min-h-0">
      <header className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3">
        <div>
          <p className="text-lg font-semibold tracking-tight">Meta-Graph</p>
          <p className="text-xs text-muted-foreground">
            Grafo di entità, eventi e concetti
          </p>
        </div>
        <div className="flex gap-2 lg:hidden">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPipelineOpen(true)}
          >
            <Activity className="mr-2 h-4 w-4" />
            Pipeline
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setQueryOpen(true)}
          >
            <MessageSquareText className="mr-2 h-4 w-4" />
            Query
          </Button>
        </div>
      </header>

      <PipelineEventBridge />

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-3 lg:flex-row lg:gap-3">
        <div className="min-h-[420px] min-w-0 flex-1 overflow-hidden">
          <EntityEventExplorer
            highlightIds={highlightIds}
            onHighlightChange={setHighlightIds}
          />
        </div>
        <aside className="hidden w-full shrink-0 flex-col gap-3 lg:flex lg:w-80">
          <div className="min-h-[180px] flex-1">
            <PipelineMonitorPanel />
          </div>
          <div className="min-h-[180px] flex-1">
            <NodeQueryPanel
              highlightIds={highlightIds}
              onHighlightChange={setHighlightIds}
            />
          </div>
        </aside>
      </div>

      <div className="flex shrink-0 gap-2 border-t border-border p-3 lg:hidden">
        <Button
          variant="outline"
          className="flex-1"
          onClick={() => setPipelineOpen(true)}
        >
          <Activity className="mr-2 h-4 w-4" />
          Pipeline
        </Button>
        <Button
          variant="outline"
          className="flex-1"
          onClick={() => setQueryOpen(true)}
        >
          <MessageSquareText className="mr-2 h-4 w-4" />
          Query
        </Button>
      </div>

      <Sheet open={pipelineOpen} onOpenChange={setPipelineOpen}>
        <SheetContent side="right" className="w-full sm:max-w-md">
          <SheetHeader>
            <SheetTitle>Pipeline Monitor</SheetTitle>
          </SheetHeader>
          <div className="mt-4 h-[calc(100vh-8rem)]">
            <PipelineMonitorPanel />
          </div>
        </SheetContent>
      </Sheet>

      <Sheet open={queryOpen} onOpenChange={setQueryOpen}>
        <SheetContent side="bottom" className="h-[50vh]">
          <SheetHeader>
            <SheetTitle>Query Entità/Eventi</SheetTitle>
          </SheetHeader>
          <div className="mt-4 h-[calc(50vh-6rem)]">
            <NodeQueryPanel
              highlightIds={highlightIds}
              onHighlightChange={setHighlightIds}
            />
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
