import { describe, expect, it } from "vitest";

import { formatPeso } from "./documents";

describe("formatPeso", () => {
  it("renders bytes, kilobytes and megabytes", () => {
    expect(formatPeso(0)).toBe("0 B");
    expect(formatPeso(512)).toBe("512 B");
    expect(formatPeso(2048)).toBe("2 KB");
    expect(formatPeso(2167)).toBe("2.1 KB");
    expect(formatPeso(1024 * 1024)).toBe("1 MB");
  });
});
