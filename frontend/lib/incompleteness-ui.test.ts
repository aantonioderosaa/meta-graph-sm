import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

describe("Macrotask 7 incompleteness UI", () => {
  it("DashboardShell tab label is exactly Visualizza incompletezze", () => {
    const shell = readFileSync(
      join(frontendRoot, "components/DashboardShell.tsx"),
      "utf8",
    );
    expect(shell).toContain("Visualizza incompletezze");
    expect(shell).toContain("IncompletenessPanel");
  });

  it("empty list copy is explicit, not an error", () => {
    const panel = readFileSync(
      join(frontendRoot, "components/IncompletenessPanel.tsx"),
      "utf8",
    );
    expect(panel).toContain('INCOMPLETENESS_EMPTY_COPY = "Nessun evento incompleto."');
    expect(panel).toContain("Incompletezze non disponibili");
    expect(panel).toContain("INCOMPLETENESS_EMPTY_COPY");
  });
});
