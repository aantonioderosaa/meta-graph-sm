"use client";

/**
 * Vista generale: one NVL canvas (GraphPanel) on GET /graph/macro.
 * Captions are names (or relation_count on collapsed edges). Metadata is one click away.
 */

import { GraphPanel } from "@/components/GraphPanel";
import { getMacroGraph } from "@/lib/api-client";
import { colorByKernelCategory } from "@/lib/graph-encoding";
import type { GraphNode, GraphRelationship } from "@/lib/types";

export function MacroGraphPanel({
  onNodeClick,
  onRelationshipClick,
  selectedId = null,
}: {
  onNodeClick?: (id: string, node: GraphNode) => void;
  onRelationshipClick?: (rel: GraphRelationship) => void;
  selectedId?: string | null;
} = {}) {
  return (
    <GraphPanel
      title="Vista generale"
      fetcher={() => getMacroGraph()}
      colorForNode={colorByKernelCategory}
      onNodeClick={onNodeClick}
      onRelationshipClick={onRelationshipClick}
      selectedId={selectedId}
      emptyMessage="Nessun concetto o nodo di primo livello nel grafo."
    />
  );
}
