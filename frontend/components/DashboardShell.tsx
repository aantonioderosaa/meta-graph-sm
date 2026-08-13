"use client";

/**
 * Dashboard shell: Graph Explorer (center) + Pipeline Monitor + Query Panel.
 * Desktop: side/bottom panels with Sheet toggles. Mobile: Tabs (E6.1).
 * Graph area toggles Fatti (existing GraphExplorer) vs Entità/Eventi (M7).
 */

import { useState } from "react";
import { Activity, MessageSquareText, Network } from "lucide-react";

import { EntityEventExplorer } from "@/components/EntityEventExplorer";
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
import { cn } from "@/lib/utils";

function GraphViewTabs({ className }: { className?: string }) {
  return (
    <Tabs
      defaultValue="facts"
      className={cn("flex h-full min-h-0 flex-col overflow-hidden", className)}
    >
      <TabsList className="mb-2 h-8 w-fit shrink-0">
        <TabsTrigger value="facts" className="px-3 text-xs">
          Fatti
        </TabsTrigger>
        <TabsTrigger value="entities" className="px-3 text-xs">
          Entità/Eventi
        </TabsTrigger>
      </TabsList>
      <TabsContent
        value="facts"
        className="mt-0 min-h-0 flex-1 overflow-hidden data-[state=inactive]:hidden"
      >
        <div className="h-full min-h-0">
          <GraphExplorerPanel />
        </div>
      </TabsContent>
      <TabsContent
        value="entities"
        className="mt-0 min-h-0 flex-1 overflow-hidden data-[state=inactive]:hidden"
      >
        <div className="h-full min-h-0">
          <EntityEventExplorer />
        </div>
      </TabsContent>
    </Tabs>
  );
}

export function DashboardShell() {
  const [pipelineOpen, setPipelineOpen] = useState(false);
  const [queryOpen, setQueryOpen] = useState(false);

  return (
    <div className="flex min-h-screen flex-col overflow-x-hidden bg-background md:h-[calc(100dvh-2.75rem)] md:min-h-0">
      <header className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3">
        <div>
          <p className="text-lg font-semibold tracking-tight">Meta-Graph</p>
          <p className="text-xs text-muted-foreground">
            Grafo dei fatti, entità ed eventi
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
      <div className="hidden min-h-0 flex-1 flex-col gap-3 overflow-hidden p-3 md:flex lg:flex-row">
        <div className="min-h-[420px] min-w-0 flex-1 overflow-hidden lg:min-h-0">
          <GraphViewTabs />
        </div>
        <aside className="flex w-full shrink-0 flex-col gap-3 lg:w-80">
          <div className="min-h-[180px] flex-1">
            <PipelineMonitorPanel />
          </div>
          <div className="min-h-[180px] flex-1">
            <QueryPanel />
          </div>
        </aside>
      </div>

      {/* Mobile: collapse into tabs */}
      <div className="flex min-h-0 flex-1 flex-col overflow-x-hidden p-3 md:hidden">
        <Tabs defaultValue="graph" className="flex min-h-0 flex-1 flex-col">
          <TabsList className="grid w-full shrink-0 grid-cols-3">
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
          <TabsContent
            value="graph"
            className="mt-3 min-h-0 flex-1 overflow-x-hidden overflow-y-auto"
          >
            <GraphViewTabs className="min-h-[420px]" />
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
