/**
 * Shared Zustand store for Graph Explorer ↔ Pipeline Monitor ↔ Query Panel (E6.3).
 * Slices are independent — no circular imports between them.
 */

import { create } from "zustand";

import type {
  GraphNode,
  GraphRelationship,
  PipelineEvent,
  Subgraph,
} from "./types";

interface GraphSlice {
  nodes: GraphNode[];
  relationships: GraphRelationship[];
  selectedFactId: string | null;
  historyHighlight: {
    nodeIds: string[];
    relIds: string[];
  } | null;
  onlyLatest: boolean;
  /** Node/rel ids currently pulsing from pipeline events (E8.3). */
  pulsingIds: string[];
  setGraph: (nodes: GraphNode[], relationships: GraphRelationship[]) => void;
  setSelectedFactId: (id: string | null) => void;
  setHistoryHighlight: (
    highlight: { nodeIds: string[]; relIds: string[] } | null,
  ) => void;
  setOnlyLatest: (value: boolean) => void;
  pulseEntities: (ids: string[]) => void;
}

interface PipelineSlice {
  pipelineEvents: PipelineEvent[];
  lastPipelineEvent: PipelineEvent | null;
  pushPipelineEvent: (event: PipelineEvent) => void;
  clearPipelineEvents: () => void;
}

interface QuerySlice {
  querySubgraph: Subgraph | null;
  setQuerySubgraph: (subgraph: Subgraph | null) => void;
  clearHighlight: () => void;
}

export type AppStore = GraphSlice & PipelineSlice & QuerySlice;

export const useAppStore = create<AppStore>((set) => ({
  // graph
  nodes: [],
  relationships: [],
  selectedFactId: null,
  historyHighlight: null,
  onlyLatest: true,
  pulsingIds: [],
  setGraph: (nodes, relationships) => set({ nodes, relationships }),
  setSelectedFactId: (selectedFactId) => set({ selectedFactId }),
  setHistoryHighlight: (historyHighlight) => set({ historyHighlight }),
  setOnlyLatest: (onlyLatest) => set({ onlyLatest }),
  pulseEntities: (ids) => {
    if (ids.length === 0) return;
    set((state) => ({
      pulsingIds: Array.from(new Set([...state.pulsingIds, ...ids])),
    }));
    const clear = () =>
      set((state) => ({
        pulsingIds: state.pulsingIds.filter((id) => !ids.includes(id)),
      }));
    globalThis.setTimeout(clear, 600);
  },

  // pipelineEvents
  pipelineEvents: [],
  lastPipelineEvent: null,
  pushPipelineEvent: (event) =>
    set((state) => ({
      pipelineEvents: [...state.pipelineEvents, event],
      lastPipelineEvent: event,
    })),
  clearPipelineEvents: () =>
    set({ pipelineEvents: [], lastPipelineEvent: null }),

  // querySubgraph
  querySubgraph: null,
  setQuerySubgraph: (querySubgraph) => set({ querySubgraph }),
  clearHighlight: () => set({ querySubgraph: null }),
}));
