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
  setGraph: (nodes: GraphNode[], relationships: GraphRelationship[]) => void;
  setSelectedFactId: (id: string | null) => void;
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
  setGraph: (nodes, relationships) => set({ nodes, relationships }),
  setSelectedFactId: (selectedFactId) => set({ selectedFactId }),

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
