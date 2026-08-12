import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  formatQueryHistoryLabel,
  loadQueryFromHistory,
} from "./query-history";
import type { QueryHistoryEntry } from "./types";

const getQueryLogDetail = vi.fn();
const postQuery = vi.fn();

vi.mock("./api-client", () => ({
  getQueryLogDetail: (...args: unknown[]) => getQueryLogDetail(...args),
  postQuery: (...args: unknown[]) => postQuery(...args),
}));

describe("query history helpers (F4)", () => {
  beforeEach(() => {
    getQueryLogDetail.mockReset();
    postQuery.mockReset();
  });

  it("formats history options with truncated text", () => {
    const entry: QueryHistoryEntry = {
      id: "q1",
      text: "A".repeat(50),
      created_at: "2026-01-02T03:04:05Z",
    };
    const label = formatQueryHistoryLabel(entry, 40);
    expect(label.startsWith(`${"A".repeat(39)}…`)).toBe(true);
    expect(label).toContain("·");
  });

  it("renders a select-ready list from a history fixture (most recent first)", () => {
    const fixture: QueryHistoryEntry[] = [
      { id: "newer", text: "Second question", created_at: "2026-02-02T00:00:00Z" },
      { id: "older", text: "First question", created_at: "2026-01-01T00:00:00Z" },
    ];
    const options = fixture.map((e) => ({
      value: e.id,
      label: formatQueryHistoryLabel(e),
    }));
    expect(options[0]?.value).toBe("newer");
    expect(options[1]?.value).toBe("older");
    expect(options[0]?.label).toContain("Second question");
  });

  it("loads history detail without calling postQuery", async () => {
    const snapshot = {
      answer: "Past answer",
      facts_used: [],
      cited_fact_ids: [],
      subgraph: { nodes: [], relationships: [] },
    };
    getQueryLogDetail.mockResolvedValue(snapshot);

    const result = await loadQueryFromHistory("q-past");

    expect(result).toEqual(snapshot);
    expect(getQueryLogDetail).toHaveBeenCalledWith("q-past");
    expect(postQuery).not.toHaveBeenCalled();
  });
});
