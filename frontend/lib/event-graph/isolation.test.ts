import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join } from "node:path";

import { describe, expect, it } from "vitest";

const FORBIDDEN_SPECIFIERS = [
  "@/lib/api-client",
  "@/lib/store",
  "@/lib/types",
  "@/lib/graph-encoding",
  "@/components/DashboardShell",
];

function walkSources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      out.push(...walkSources(full));
      continue;
    }
    if (!/\.(ts|tsx)$/.test(extname(name))) continue;
    if (name.endsWith(".test.ts") || name.endsWith(".test.tsx")) continue;
    out.push(full);
  }
  return out;
}

function hasExactSpecifier(source: string, specifier: string): boolean {
  return source.includes(`"${specifier}"`) || source.includes(`'${specifier}'`);
}

describe("event-graph isolation", () => {
  it("does not import legacy lib or DashboardShell", () => {
    const roots = [
      join(__dirname),
      join(__dirname, "../../components/event-graph"),
    ];
    const files = roots.flatMap((root) => walkSources(root));
    expect(files.length).toBeGreaterThan(0);

    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const specifier of FORBIDDEN_SPECIFIERS) {
        expect(
          hasExactSpecifier(source, specifier),
          `${file} imports forbidden ${specifier}`,
        ).toBe(false);
      }
    }
  });
});
