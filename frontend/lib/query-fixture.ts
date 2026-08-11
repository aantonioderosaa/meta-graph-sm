/**
 * Offline query fixture for E9.1 demo/tests.
 * Not used by QueryPanel when talking to the real POST /query (E9.3).
 */

import type { QueryResponse } from "./types";

export const QUERY_FIXTURE_RESPONSE: QueryResponse = {
  answer:
    "Alice works at Acme Corp and prefers remote work. (fixture — use POST /query for live answers)",
  facts_used: [
    {
      id: "fact-a",
      text: "Alice works at Acme Corp.",
      source_doc_id: "doc-stub-1",
    },
    {
      id: "fact-b",
      text: "Alice prefers remote work.",
      source_doc_id: "doc-stub-1",
    },
  ],
  subgraph: {
    nodes: [
      {
        id: "fact-a",
        label: "Fact",
        properties: { text: "Alice works at Acme Corp.", type: "fact", is_latest: true },
      },
      {
        id: "fact-b",
        label: "Fact",
        properties: {
          text: "Alice prefers remote work.",
          type: "preference",
          is_latest: true,
        },
      },
    ],
    relationships: [
      { source: "fact-b", target: "fact-a", type: "extends" },
    ],
  },
};
