import { describe, expect, it } from "vitest";

import { citationBadge, listCitationBadges } from "./citation-badges";
import type { NodeQueryResponse } from "./types";

const derived: NodeQueryResponse = {
  answer: "ok",
  nodes_used: [],
  concepts_used: [],
  cited_node_ids: ["n1"],
  subgraph: { nodes: [], relationships: [] },
  citations: [
    { id: "n1", epistemic_status: "asserted" },
    {
      id: "a|works_at|b",
      epistemic_status: "derived",
      derivation_chain: [{ kind: "s1", detail: "Agente —works_at→ CostruttoSociale" }],
    },
  ],
};

describe("citationBadge", () => {
  it("prefers citations over cited_node_ids", () => {
    const asserted = citationBadge("n1", derived);
    expect(asserted?.label).toBe("ASSERITO");
    expect(asserted?.variant).toBe("muted");
    expect(asserted?.chain).toEqual([]);

    const hop = citationBadge("a|works_at|b", derived);
    expect(hop?.label).toBe("DERIVATO");
    expect(hop?.variant).toBe("warning");
    expect(hop?.chain).toHaveLength(1);
    expect(hop?.chain[0].kind).toBe("s1");
  });

  it("falls back to cited_node_ids as asserted when citations are absent", () => {
    const badge = citationBadge("n1", {
      cited_node_ids: ["n1", "n2"],
    });
    expect(badge?.label).toBe("ASSERITO");
    expect(citationBadge("missing", { cited_node_ids: ["n1"] })).toBeNull();
  });
});

describe("listCitationBadges", () => {
  it("lists citations when present", () => {
    const badges = listCitationBadges(derived);
    expect(badges.map((b) => b.label)).toEqual(["ASSERITO", "DERIVATO"]);
  });

  it("maps cited_node_ids when citations are empty", () => {
    const badges = listCitationBadges({ cited_node_ids: ["a"], citations: [] });
    expect(badges).toEqual([
      {
        id: "a",
        status: "asserted",
        label: "ASSERITO",
        variant: "muted",
        chain: [],
      },
    ]);
  });
});
