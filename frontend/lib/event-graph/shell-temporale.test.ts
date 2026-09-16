import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const panelPath = join(
  __dirname,
  "../../components/event-graph/EventGraphPanel.tsx",
);
const shellPath = join(
  __dirname,
  "../../components/event-graph/EventGraphShell.tsx",
);

describe("EventGraphPanel relation names on edges", () => {
  it("draws the tipo on Tutto/Relazioni edges with a colored mid-line label", () => {
    const source = readFileSync(panelPath, "utf8");
    expect(source).toContain("ladderEdgeLabel");
    expect(source).toContain("text-background-color");
    expect(source).toContain("useNamedEdges");
    expect(source).not.toMatch(/useCose[\s\S]*label:\s*"data\(spiegazione\)"/);
  });
});

describe("EventGraphShell temporale senza ancore", () => {
  it("keeps original Tutto/Relazioni graph layouts", () => {
    const source = readFileSync(shellPath, "utf8");
    expect(source).toMatch(/tutto:\s*"dagre"/);
    expect(source).toMatch(/relazioni:\s*"cose"/);
    expect(source).not.toMatch(/tutto:\s*"tutto"/);
  });

  it("shows explicit copy instead of an empty graph or fallback box", () => {
    const source = readFileSync(shellPath, "utf8");
    expect(source).toContain("nessuna ancora temporale in questo documento");
    expect(source).toContain("nessunaAncoraTemporale");
    expect(source).toMatch(
      /nessunaAncoraTemporale\s*\?[\s\S]*nessuna ancora temporale in questo documento[\s\S]*:\s*\([\s\S]*EventGraphPanel/,
    );
    expect(source).not.toMatch(/fallback box|secchio|collocazione-ignota/i);
  });
});
