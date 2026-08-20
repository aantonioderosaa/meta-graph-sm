import type { GraphNode, NodeMetadataResponse } from "./types";

/** Facet card under metadata only when the node already has identity URIs. */
export function shouldEmbedIdentityPanel(data: NodeMetadataResponse): boolean {
  return data.kind === "node" && (data.identity_uris?.length ?? 0) > 0;
}

/** Push a domain onto the nested-scope stack. No-op if already current. */
export function pushDrill(path: string[], id: string): string[] {
  const trimmed = id.trim();
  if (!trimmed) return path;
  if (path[path.length - 1] === trimmed) return path;
  return [...path, trimmed];
}

/** Pop the current nested scope. Root (`[]`) is a no-op. */
export function popDrill(path: string[]): string[] {
  if (path.length === 0) return path;
  return path.slice(0, -1);
}

export function currentDrillId(path: string[]): string | null {
  return path.length === 0 ? null : path[path.length - 1]!;
}

export function partitionDomainChildren(nodes: GraphNode[]): {
  concepts: GraphNode[];
  members: GraphNode[];
} {
  const concepts: GraphNode[] = [];
  const members: GraphNode[] = [];
  for (const node of nodes) {
    const type = String(node.properties?.type ?? "");
    if (type === "concept") concepts.push(node);
    else members.push(node);
  }
  return { concepts, members };
}

export function nodeTypeLabel(nodeType: string | null | undefined): string | null {
  if (nodeType === "event") return "Evento";
  if (nodeType === "entity") return "Entità";
  return nodeType ? String(nodeType) : null;
}
