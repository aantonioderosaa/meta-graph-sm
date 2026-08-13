"use client";

/**
 * Four-panel entity / concept / event / participation explorer.
 * Each GraphPanel owns local graph state. Highlight is parent-owned when
 * DashboardShell passes props.
 */

import { useCallback, useState } from "react";

import { GraphPanel } from "@/components/GraphPanel";
import {
  getConceptNeighbors,
  getConceptOverview,
  getEntityGraph,
  getEventGraph,
  getParticipationGraph,
} from "@/lib/api-client";
import { idsFromConceptNeighbors } from "@/lib/concept-neighbors";

export function EntityEventExplorer({
  highlightIds: highlightIdsProp,
  onHighlightChange,
}: {
  highlightIds?: Set<string> | null;
  onHighlightChange?: (ids: Set<string> | null) => void;
} = {}) {
  const [internalHighlight, setInternalHighlight] = useState<Set<string> | null>(
    null,
  );
  const [focusedConceptId, setFocusedConceptId] = useState<string | null>(null);

  const highlightIds =
    highlightIdsProp !== undefined ? highlightIdsProp : internalHighlight;

  const focusConcept = useCallback(
    async (id: string) => {
      setFocusedConceptId(id);
      try {
        const graph = await getConceptNeighbors(id);
        const ids = new Set(idsFromConceptNeighbors(graph, id));
        if (onHighlightChange) {
          onHighlightChange(ids);
        } else {
          setInternalHighlight(ids);
        }
      } catch {
        if (onHighlightChange) {
          onHighlightChange(null);
        } else {
          setInternalHighlight(null);
        }
      }
    },
    [onHighlightChange],
  );

  return (
    <div
      aria-label="Esploratore entità e eventi"
      className="grid h-full min-h-0 grid-cols-1 gap-3 overflow-x-hidden overflow-y-auto lg:grid-cols-3 lg:grid-rows-[minmax(0,1fr)_minmax(180px,32%)] lg:overflow-hidden"
    >
      <GraphPanel
        title="Entità"
        fetcher={() => getEntityGraph({ is_latest: true })}
        highlightIds={highlightIds}
        emptyMessage="Nessuna entità nel grafo."
      />
      <GraphPanel
        title="Concetti"
        fetcher={() => getConceptOverview()}
        onNodeClick={(id) => void focusConcept(id)}
        selectedId={focusedConceptId}
        highlightIds={highlightIds}
        emptyMessage="Nessun concetto nel grafo."
      />
      <GraphPanel
        title="Eventi"
        fetcher={() => getEventGraph({ is_latest: true })}
        highlightIds={highlightIds}
        emptyMessage="Nessun evento nel grafo."
      />
      <GraphPanel
        title="Partecipazione"
        fetcher={() => getParticipationGraph()}
        className="lg:col-span-3"
        highlightIds={highlightIds}
        emptyMessage="Nessuna partecipazione nel grafo."
      />
    </div>
  );
}
