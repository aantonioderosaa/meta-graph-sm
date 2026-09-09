import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const frontendRoot = join(__dirname, "../..");

describe("legacy UI unmounted", () => {
  it("does not keep the Next.js documents route", () => {
    expect(existsSync(join(frontendRoot, "app/documents/page.tsx"))).toBe(false);
  });

  it("home page exists and mounts EventGraphShell", () => {
    const pagePath = join(frontendRoot, "app/page.tsx");
    expect(existsSync(pagePath)).toBe(true);
    expect(readFileSync(pagePath, "utf8")).toContain("EventGraphShell");
  });

  it("keeps leftover legacy components on disk", () => {
    expect(existsSync(join(frontendRoot, "components/AppShell.tsx"))).toBe(true);
    expect(existsSync(join(frontendRoot, "components/DashboardShell.tsx"))).toBe(
      true,
    );
    expect(existsSync(join(frontendRoot, "components/DocumentsPage.tsx"))).toBe(
      true,
    );
  });

  it("root layout does not import AppShell or DashboardShell", () => {
    const layout = readFileSync(join(frontendRoot, "app/layout.tsx"), "utf8");
    expect(layout).not.toMatch(/AppShell/);
    expect(layout).not.toMatch(/DashboardShell/);
  });
});
