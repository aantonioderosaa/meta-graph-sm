import { describe, expect, it } from "vitest";

import {
  bundleEdgeId,
  collapsePairCounts,
  endpointsFromBundleRel,
  parseBundleRelId,
} from "./bundle";
import { citationBadge, listCitationBadges } from "./citation-badges";

describe("bundle pair collapse and endpoint parsing", () => {
  it("orders undirected ids as bundle:from:to with from < to", () => {
    expect(bundleEdgeId("b", "a")).toBe("bundle:a:b");
    expect(parseBundleRelId("bundle:a:b")).toEqual({ a: "a", b: "b" });
    expect(parseBundleRelId("not-a-bundle")).toBeNull();
  });

  it("parses endpoints from a GraphRelationship id or from/to fallback", () => {
    expect(
      endpointsFromBundleRel({ id: "bundle:alice:sport", from: "x", to: "y" }),
    ).toEqual({ a: "alice", b: "sport" });
    expect(
      endpointsFromBundleRel({ id: "other", from: "z", to: "a" }),
    ).toEqual({ a: "a", b: "z" });
  });

  it("collapses leaf pairs onto promoted concepts with relation_count", () => {
    const counts = collapsePairCounts(
      [
        ["p1", "c1"],
        ["p1", "c1"],
        ["c2", "p2"],
      ],
      new Set(["sport", "club"]),
      { p1: "sport", p2: "sport", c1: "club", c2: "club" },
    );
    expect(counts.get(bundleEdgeId("sport", "club"))).toBe(3);
  });

  it("reuses citation badges as ASSERITO when epistemic_status is asserted", () => {
    const source = {
      citations: [{ id: "rel-1", epistemic_status: "asserted" as const }],
      cited_node_ids: ["rel-1"],
    };
    expect(citationBadge("rel-1", source)?.label).toBe("ASSERITO");
    expect(listCitationBadges(source)[0].label).toBe("ASSERITO");
  });
});
