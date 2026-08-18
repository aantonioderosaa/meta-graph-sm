/**
 * ASSERITO / DERIVATO badges for NodeQueryPanel citations (Fase 11 → F12.6).
 */

import type { DerivationStep, NodeQueryResponse, QueryCitation } from "./types";

export type CitationBadgeStatus = "asserted" | "derived";

export type CitationBadge = {
  id: string;
  status: CitationBadgeStatus;
  label: "ASSERITO" | "DERIVATO";
  variant: "muted" | "warning";
  chain: DerivationStep[];
};

function badgeFromCitation(citation: QueryCitation): CitationBadge {
  const derived = citation.epistemic_status === "derived";
  return {
    id: citation.id,
    status: derived ? "derived" : "asserted",
    label: derived ? "DERIVATO" : "ASSERITO",
    variant: derived ? "warning" : "muted",
    chain: derived ? (citation.derivation_chain ?? []) : [],
  };
}

function assertedBadge(id: string): CitationBadge {
  return {
    id,
    status: "asserted",
    label: "ASSERITO",
    variant: "muted",
    chain: [],
  };
}

/** Prefer `citations`; fall back to `cited_node_ids` as asserted. */
export function listCitationBadges(
  response: Pick<NodeQueryResponse, "citations" | "cited_node_ids">,
): CitationBadge[] {
  if (response.citations && response.citations.length > 0) {
    return response.citations.map(badgeFromCitation);
  }
  return (response.cited_node_ids ?? []).map(assertedBadge);
}

export function citationBadge(
  id: string,
  response: Pick<NodeQueryResponse, "citations" | "cited_node_ids">,
): CitationBadge | null {
  const citations = response.citations;
  if (citations && citations.length > 0) {
    const hit = citations.find((c) => c.id === id);
    return hit ? badgeFromCitation(hit) : null;
  }
  if (response.cited_node_ids?.includes(id)) {
    return assertedBadge(id);
  }
  return null;
}
