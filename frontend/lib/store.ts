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
  /** Active SSE job — owned globally so stream survives route changes (F3.2). */
  activeJobId: string | null;
  streamRestartToken: number;
  pushPipelineEvent: (event: PipelineEvent) => void;
  clearPipelineEvents: () => void;
  setActiveJobId: (id: string | null) => void;
  bumpStreamRestart: () => void;
}

interface QuerySlice {
  querySubgraph: Subgraph | null;
  setQuerySubgraph: (subgraph: Subgraph | null) => void;
  clearHighlight: () => void;
}

/** Bumped after DELETE /graph so mounted views can drop local UI state (R3.4). */
interface ResetSlice {
  kbResetEpoch: number;
  notifyKnowledgeBaseReset: () => void;
}

export type AppStore = GraphSlice & PipelineSlice & QuerySlice & ResetSlice;

/** Perceived pulse duration per id (ms). */
export const PULSE_DURATION_MS = 600;
/** Shared sweep interval for expired pulses (ms). */
export const PULSE_TICK_MS = 150;

const pulseExpiryById = new Map<string, number>();
let pulseTimer: ReturnType<typeof setInterval> | null = null;

function sweepExpiredPulses(): void {
  const now = Date.now();
  let removed = false;
  for (const [id, expiresAt] of pulseExpiryById) {
    if (expiresAt <= now) {
      pulseExpiryById.delete(id);
      removed = true;
    }
  }
  if (removed || pulseExpiryById.size === 0) {
    useAppStore.setState({ pulsingIds: Array.from(pulseExpiryById.keys()) });
  }
  if (pulseExpiryById.size === 0 && pulseTimer !== null) {
    globalThis.clearInterval(pulseTimer);
    pulseTimer = null;
  }
}

/** Test helper: clear module-level pulse timer/map between cases. */
export function resetPulseSchedulerForTests(): void {
  if (pulseTimer !== null) {
    globalThis.clearInterval(pulseTimer);
    pulseTimer = null;
  }
  pulseExpiryById.clear();
}

/** Test helper: whether the shared pulse interval is armed. */
export function isPulseTimerActiveForTests(): boolean {
  return pulseTimer !== null;
}

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
    const expiresAt = Date.now() + PULSE_DURATION_MS;
    for (const id of ids) {
      pulseExpiryById.set(id, expiresAt);
    }
    set({ pulsingIds: Array.from(pulseExpiryById.keys()) });
    if (pulseTimer === null) {
      pulseTimer = globalThis.setInterval(sweepExpiredPulses, PULSE_TICK_MS);
    }
  },

  // pipelineEvents
  pipelineEvents: [],
  lastPipelineEvent: null,
  activeJobId: null,
  streamRestartToken: 0,
  pushPipelineEvent: (event) =>
    set((state) => ({
      pipelineEvents: [...state.pipelineEvents, event],
      lastPipelineEvent: event,
    })),
  clearPipelineEvents: () =>
    set({ pipelineEvents: [], lastPipelineEvent: null }),
  setActiveJobId: (activeJobId) => set({ activeJobId }),
  bumpStreamRestart: () =>
    set((state) => ({ streamRestartToken: state.streamRestartToken + 1 })),

  // querySubgraph
  querySubgraph: null,
  setQuerySubgraph: (querySubgraph) => set({ querySubgraph }),
  clearHighlight: () => set({ querySubgraph: null }),

  // knowledge-base reset (R3.4)
  kbResetEpoch: 0,
  notifyKnowledgeBaseReset: () => {
    pulseExpiryById.clear();
    if (pulseTimer !== null) {
      globalThis.clearInterval(pulseTimer);
      pulseTimer = null;
    }
    set((state) => ({
      nodes: [],
      relationships: [],
      selectedFactId: null,
      historyHighlight: null,
      pulsingIds: [],
      pipelineEvents: [],
      lastPipelineEvent: null,
      activeJobId: null,
      querySubgraph: null,
      kbResetEpoch: state.kbResetEpoch + 1,
    }));
  },
}));
