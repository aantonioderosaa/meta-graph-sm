"use client";

/**
 * Dashboard shell: Graph Explorer (center) + Pipeline Monitor + Query Panel.
 * Desktop: side/bottom panels with Sheet toggles. Mobile: Tabs (E6.1).
 */

import { useState } from "react";
import { Activity, MessageSquareText, Network } from "lucide-react";

import { GraphExplorerPanel } from "@/components/GraphExplorerPanel";
import { PipelineEventBridge } from "@/components/PipelineEventBridge";
import { PipelineMonitorPanel } from "@/components/PipelineMonitorPanel";
import { QueryPanel } from "@/components/QueryPanel";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function DashboardShell() {
  const [pipelineOpen, setPipelineOpen] = useState(false);
  const [queryOpen, setQueryOpen] = useState(false);

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <p className="text-lg font-semibold tracking-tight">Meta-Graph</p>
          <p className="text-xs text-muted-foreground">
            Motore del grafo dei fatti — Milestone 1
          </p>
        </div>
        <div className="hidden gap-2 md:flex">
          <Sheet open={pipelineOpen} onOpenChange={setPipelineOpen}>
            <SheetTrigger asChild>
              <Button variant="outline" size="sm">
                <Activity className="mr-2 h-4 w-4" />
                Pipeline
              </Button>
            </SheetTrigger>
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
            <SheetTrigger asChild>
              <Button variant="outline" size="sm">
                <MessageSquareText className="mr-2 h-4 w-4" />
                Query
              </Button>
            </SheetTrigger>
            <SheetContent side="bottom" className="h-[50vh]">
              <SheetHeader>
                <SheetTitle>Query Panel</SheetTitle>
              </SheetHeader>
              <div className="mt-4 h-[calc(50vh-6rem)]">
                <QueryPanel />
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </header>

      <PipelineEventBridge />

      {/* Desktop / tablet: graph dominates; side panels visible below */}
      <div className="hidden flex-1 flex-col gap-3 p-3 md:flex lg:flex-row">
        <div className="min-h-[420px] flex-1 lg:min-h-0">
          <GraphExplorerPanel />
        </div>
        <aside className="flex w-full flex-col gap-3 lg:w-80 lg:shrink-0">
          <div className="min-h-[180px] flex-1">
            <PipelineMonitorPanel />
          </div>
          <div className="min-h-[180px] flex-1">
            <QueryPanel />
          </div>
        </aside>
      </div>

      {/* Mobile: collapse into tabs */}
      <div className="flex flex-1 flex-col p-3 md:hidden">
        <Tabs defaultValue="graph" className="flex flex-1 flex-col">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="graph">
              <Network className="mr-1 h-3.5 w-3.5" />
              Grafo
            </TabsTrigger>
            <TabsTrigger value="pipeline">
              <Activity className="mr-1 h-3.5 w-3.5" />
              Pipeline
            </TabsTrigger>
            <TabsTrigger value="query">
              <MessageSquareText className="mr-1 h-3.5 w-3.5" />
              Query
            </TabsTrigger>
          </TabsList>
          <TabsContent value="graph" className="mt-3 flex-1">
            <GraphExplorerPanel />
          </TabsContent>
          <TabsContent value="pipeline" className="mt-3 flex-1">
            <PipelineMonitorPanel />
          </TabsContent>
          <TabsContent value="query" className="mt-3 flex-1">
            <QueryPanel />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
