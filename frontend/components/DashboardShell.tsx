"use client";

/**
 * Dashboard shell: single view, no toggle. The legacy four-panel explorer
 * (entity/event/participation/concept) is retired — DomainGraphPanel is the
 * only graph, mounted directly. Graph sits on top (primary, tallest row);
 * DomainDashboard + detail column sit below it. The old free-tag concept
 * bridge (HAS_CONCEPT) is retired too: ConceptDomainExplorer is gone, its
 * "Dominio" tab removed — domain browsing lives only in the kernel-backed
 * DomainDashboard now. Tabbed Pipeline / Query / Metagraph layer panels stay
 * lists. Desktop sidebar at lg; mobile uses Sheets — never a second graph.
 */

import { useState } from "react";
import { Activity, Layers, MessageSquareText } from "lucide-react";

import { BundleDetailPanel } from "@/components/BundleDetailPanel";
import { ConnectivityRulesPanel } from "@/components/ConnectivityRulesPanel";
import { ContradictionsPanel } from "@/components/ContradictionsPanel";
import { DomainDashboard } from "@/components/DomainDashboard";
import { DomainDetailCard } from "@/components/DomainDetailCard";
import { DomainGraphPanel } from "@/components/DomainGraphPanel";
import { IncompletenessPanel } from "@/components/IncompletenessPanel";
import { JudgeLogPanel } from "@/components/JudgeLogPanel";
import { NodeMetadataPanel } from "@/components/NodeMetadataPanel";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { endpointsFromBundleRel } from "@/lib/bundle";
import { popDrill, pushDrill } from "@/lib/domain-nav";
import type { DomainListItem, GraphNode, GraphRelationship } from "@/lib/types";

function LayerTabs({
  highlightIds,
  onHighlightChange,
  defaultValue = "pipeline",
}: {
  highlightIds: Set<string> | null;
  onHighlightChange: (ids: Set<string> | null) => void;
  defaultValue?: string;
}) {
  return (
    <Tabs defaultValue={defaultValue} className="flex h-full min-h-0 flex-col">
      <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1 bg-muted/60 p-1">
        <TabsTrigger value="pipeline" className="px-2 text-[11px]">
          Pipeline
        </TabsTrigger>
        <TabsTrigger value="query" className="px-2 text-[11px]">
          Query
        </TabsTrigger>
        <TabsTrigger value="contraddizioni" className="px-2 text-[11px]">
          Contraddizioni
        </TabsTrigger>
        <TabsTrigger value="regole" className="px-2 text-[11px]">
          Regole
        </TabsTrigger>
        <TabsTrigger value="giudice" className="px-2 text-[11px]">
          Giudice
        </TabsTrigger>
        <TabsTrigger value="incompletezze" className="px-2 text-[11px]">
          Visualizza incompletezze
        </TabsTrigger>
      </TabsList>
      <TabsContent value="pipeline" className="mt-2 min-h-0 flex-1 overflow-hidden">
        <PipelineMonitorPanel />
      </TabsContent>
      <TabsContent value="query" className="mt-2 min-h-0 flex-1 overflow-hidden">
        <NodeQueryPanel
          highlightIds={highlightIds}
          onHighlightChange={onHighlightChange}
        />
      </TabsContent>
      <TabsContent value="contraddizioni" className="mt-2 min-h-0 flex-1 overflow-hidden">
        <ContradictionsPanel onHighlightChange={onHighlightChange} />
      </TabsContent>
      <TabsContent value="regole" className="mt-2 min-h-0 flex-1 overflow-hidden">
        <ConnectivityRulesPanel />
      </TabsContent>
      <TabsContent value="giudice" className="mt-2 min-h-0 flex-1 overflow-hidden">
        <JudgeLogPanel />
      </TabsContent>
      <TabsContent value="incompletezze" className="mt-2 min-h-0 flex-1 overflow-hidden">
        <IncompletenessPanel onHighlightChange={onHighlightChange} />
      </TabsContent>
    </Tabs>
  );
}

export function DashboardShell() {
  const [pipelineOpen, setPipelineOpen] = useState(false);
  const [queryOpen, setQueryOpen] = useState(false);
  const [layerOpen, setLayerOpen] = useState(false);
  const [highlightIds, setHighlightIds] = useState<Set<string> | null>(null);
  const [bundlePair, setBundlePair] = useState<{ a: string; b: string } | null>(
    null,
  );
  const [metadataId, setMetadataId] = useState<string | null>(null);
  const [drillPath, setDrillPath] = useState<string[]>([]);
  const [domains, setDomains] = useState<DomainListItem[]>([]);
  const [selectedDomainId, setSelectedDomainId] = useState<string | null>(null);

  const selectedDomain =
    domains.find((item) => item.id === selectedDomainId) ??
    (selectedDomainId
      ? {
          id: selectedDomainId,
          name: selectedDomainId,
          promoted: false,
          direct_member_count: 0,
        }
      : null);

  function selectDomain(id: string) {
    setSelectedDomainId(id);
    setMetadataId(null);
  }

  function onDomainGraphNodeClick(id: string, node: GraphNode) {
    const type = String(node.properties?.type ?? "");
    if (type === "concept") {
      setSelectedDomainId(id);
      setMetadataId(null);
      setDrillPath((path) => pushDrill(path, id));
      return;
    }
    setMetadataId(id);
  }

  function onDomainRelClick(rel: GraphRelationship) {
    setBundlePair(endpointsFromBundleRel(rel));
  }

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
          <Button variant="outline" size="sm" onClick={() => setLayerOpen(true)}>
            <Layers className="mr-2 h-4 w-4" />
            Layer
          </Button>
        </div>
      </header>

      <PipelineEventBridge />

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-3 lg:flex-row lg:gap-3">
        <div className="flex min-h-[420px] min-w-0 flex-1 flex-col overflow-hidden">
          <div className="mb-2 flex shrink-0 items-center gap-2 px-1 text-[10px] text-muted-foreground">
            Vista generale — grafo a scope annidato, dashboard domini sotto
          </div>
          <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)_minmax(11rem,32%)] gap-2 overflow-hidden">
            <DomainGraphPanel
              drillPath={drillPath}
              onBack={() => {
                setDrillPath((path) => {
                  const next = popDrill(path);
                  setSelectedDomainId(
                    next.length === 0 ? selectedDomainId : next[next.length - 1]!,
                  );
                  return next;
                });
              }}
              onNodeClick={onDomainGraphNodeClick}
              onRelationshipClick={onDomainRelClick}
              selectedId={selectedDomainId}
            />
            <div className="grid min-h-0 grid-cols-1 gap-2 overflow-hidden lg:grid-cols-[minmax(14rem,22rem)_minmax(0,1fr)]">
              <DomainDashboard
                selectedId={selectedDomainId}
                onSelect={(item) => selectDomain(item.id)}
                onLoaded={setDomains}
              />
              <div className="min-h-0 overflow-auto">
                {selectedDomain ? (
                  <DomainDetailCard
                    domain={selectedDomain}
                    onSelectConcept={selectDomain}
                    onSelectNode={(id) => setMetadataId(id)}
                  />
                ) : (
                  <p className="p-3 text-center text-xs text-muted-foreground">
                    Clicca un dominio nella lista per dizionario e regole.
                  </p>
                )}
                {bundlePair ? (
                  <div className="mt-2">
                    <BundleDetailPanel
                      nodeAId={bundlePair.a}
                      nodeBId={bundlePair.b}
                    />
                  </div>
                ) : null}
                {metadataId ? (
                  <div className="mt-2">
                    <NodeMetadataPanel nodeId={metadataId} />
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </div>
        <aside className="hidden w-full shrink-0 flex-col lg:flex lg:w-80">
          <LayerTabs
            highlightIds={highlightIds}
            onHighlightChange={setHighlightIds}
          />
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
        <Button
          variant="outline"
          className="flex-1"
          onClick={() => setLayerOpen(true)}
        >
          <Layers className="mr-2 h-4 w-4" />
          Layer
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

      <Sheet open={layerOpen} onOpenChange={setLayerOpen}>
        <SheetContent side="right" className="w-full sm:max-w-md">
          <SheetHeader>
            <SheetTitle>Layer Metagraph</SheetTitle>
          </SheetHeader>
          <div className="mt-4 h-[calc(100vh-8rem)]">
            <LayerTabs
              highlightIds={highlightIds}
              onHighlightChange={setHighlightIds}
            />
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
