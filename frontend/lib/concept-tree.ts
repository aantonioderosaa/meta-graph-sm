/**
 * Build a navigable IS_A / MEMBER_OF containment tree from concept graph views.
 */

import type { GraphNode, GraphRelationship, GraphResponse } from "./types";

export type ConceptTreeMember = {
  id: string;
  caption: string;
  type?: string;
};

export type ConceptTreeNode = {
  id: string;
  caption: string;
  kernel_category?: string;
  definition?: string;
  parent_uri?: string;
  children: ConceptTreeNode[];
  members: ConceptTreeMember[];
};

function asString(value: unknown): string | undefined {
  if (value === null || value === undefined || value === "") return undefined;
  return String(value);
}

export function conceptMeta(node: GraphNode): {
  kernel_category?: string;
  definition?: string;
  parent_uri?: string;
} {
  return {
    kernel_category: asString(node.properties?.kernel_category),
    definition: asString(node.properties?.definition),
    parent_uri: asString(node.properties?.parent_uri),
  };
}

function isConcept(node: GraphNode): boolean {
  return String(node.properties?.type ?? "").toLowerCase() === "concept";
}

function relType(rel: GraphRelationship): string {
  return (rel.type || rel.caption || "").toUpperCase();
}

function toTreeNode(node: GraphNode): ConceptTreeNode {
  const meta = conceptMeta(node);
  return {
    id: node.id,
    caption: node.caption || node.id,
    ...meta,
    children: [],
    members: [],
  };
}

/** Roots-first containment tree: IS_A is child→parent; MEMBER_OF is node→concept. */
export function buildConceptTree(graph: GraphResponse): ConceptTreeNode[] {
  const concepts = new Map<string, ConceptTreeNode>();
  for (const node of graph.nodes) {
    if (isConcept(node)) {
      concepts.set(node.id, toTreeNode(node));
    }
  }

  const childOf = new Map<string, string>();
  for (const rel of graph.relationships) {
    if (relType(rel) !== "IS_A") continue;
    const childId = rel.from;
    const parentId = rel.to;
    if (!concepts.has(childId) || !concepts.has(parentId)) continue;
    childOf.set(childId, parentId);
  }

  for (const rel of graph.relationships) {
    if (relType(rel) !== "MEMBER_OF") continue;
    const concept = concepts.get(rel.to);
    if (!concept) continue;
    const member = graph.nodes.find((n) => n.id === rel.from);
    if (!member || isConcept(member)) continue;
    if (concept.members.some((m) => m.id === member.id)) continue;
    concept.members.push({
      id: member.id,
      caption: member.caption || member.id,
      type: asString(member.properties?.type),
    });
  }

  const attached = new Set<string>();
  for (const [childId, parentId] of childOf) {
    if (childId === parentId) continue;
    const child = concepts.get(childId);
    const parent = concepts.get(parentId);
    if (!child || !parent) continue;
    if (parent.children.some((c) => c.id === child.id)) continue;
    parent.children.push(child);
    attached.add(childId);
  }

  return [...concepts.values()].filter((node) => !attached.has(node.id));
}

function cloneTree(nodes: ConceptTreeNode[]): ConceptTreeNode[] {
  return nodes.map((node) => ({
    ...node,
    children: cloneTree(node.children),
    members: node.members.map((member) => ({ ...member })),
  }));
}

function findNode(nodes: ConceptTreeNode[], id: string): ConceptTreeNode | null {
  for (const node of nodes) {
    if (node.id === id) return node;
    const nested = findNode(node.children, id);
    if (nested) return nested;
  }
  return null;
}

/** Merge expanded neighbors (IS_A children + MEMBER_OF members) into an existing tree. */
export function mergeConceptNeighbors(
  roots: ConceptTreeNode[],
  conceptId: string,
  neighbors: GraphResponse,
): ConceptTreeNode[] {
  const next = cloneTree(roots);
  let target = findNode(next, conceptId);
  if (!target) {
    const built = buildConceptTree(neighbors);
    return built.length > 0 ? built : next;
  }

  const childIds = new Set(
    neighbors.relationships
      .filter((rel) => relType(rel) === "IS_A" && rel.to === conceptId)
      .map((rel) => rel.from),
  );
  for (const node of neighbors.nodes) {
    if (!childIds.has(node.id) || !isConcept(node)) continue;
    if (!target.children.some((child) => child.id === node.id)) {
      target.children.push(toTreeNode(node));
    }
  }

  for (const rel of neighbors.relationships) {
    if (relType(rel) !== "MEMBER_OF" || rel.to !== conceptId) continue;
    const member = neighbors.nodes.find((node) => node.id === rel.from);
    if (!member || isConcept(member)) continue;
    if (!target.members.some((item) => item.id === member.id)) {
      target.members.push({
        id: member.id,
        caption: member.caption || member.id,
        type: asString(member.properties?.type),
      });
    }
  }

  return next;
}
