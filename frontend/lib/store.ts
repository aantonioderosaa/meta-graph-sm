/**
 * Shared Zustand store for Pipeline Monitor + knowledge-base reset.
 * Graph and query slices for the old explorer lived here; they are gone.
 */

import { create } from "zustand";

import type { PipelineEvent } from "./types";

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

/** Bumped after DELETE /graph so mounted views can drop local UI state (R3.4). */
interface ResetSlice {
  kbResetEpoch: number;
  notifyKnowledgeBaseReset: () => void;
}

export type AppStore = PipelineSlice & ResetSlice;

export const useAppStore = create<AppStore>((set) => ({
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

  kbResetEpoch: 0,
  notifyKnowledgeBaseReset: () =>
    set((state) => ({
      pipelineEvents: [],
      lastPipelineEvent: null,
      activeJobId: null,
      kbResetEpoch: state.kbResetEpoch + 1,
    })),
}));
