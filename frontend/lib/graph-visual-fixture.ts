/**
 * Fixture covering every visual-encoding case from tech-spec §11.1 (E7.2).
 * Used by unit tests / checklist — NOT imported by GraphExplorer (E7.6 swap).
 */

import type { GraphNode, GraphRelationship } from "./types";

/** 3 types × 2 is_latest states */
export const FIXTURE_NODES: GraphNode[] = [
  {
    id: "fact-latest",
    caption: "Alice works at Acme",
    properties: { type: "fact", is_latest: true, confidence: 1 },
  },
  {
    id: "fact-historical",
    caption: "Alice worked at Beta",
    properties: { type: "fact", is_latest: false, confidence: 1 },
  },
  {
    id: "pref-latest",
    caption: "Alice prefers remote",
    properties: { type: "preference", is_latest: true, confidence: 1 },
  },
  {
    id: "pref-historical",
    caption: "Alice preferred office",
    properties: { type: "preference", is_latest: false, confidence: 1 },
  },
  {
    id: "ep-latest",
    caption: "Kickoff meeting 2024",
    properties: { type: "episode", is_latest: true, confidence: 1 },
  },
  {
    id: "ep-historical",
    caption: "Kickoff meeting 2023",
    properties: { type: "episode", is_latest: false, confidence: 1 },
  },
];

/** One relationship of each type connecting latest facts (complementary EXTENDS visible together). */
export const FIXTURE_RELATIONSHIPS: GraphRelationship[] = [
  {
    id: "rel-updates",
    from: "fact-latest",
    to: "fact-historical",
    type: "UPDATES",
    caption: "updates",
  },
  {
    id: "rel-extends",
    from: "pref-latest",
    to: "fact-latest",
    type: "EXTENDS",
    caption: "extends",
  },
  {
    id: "rel-derives",
    from: "ep-latest",
    to: "fact-latest",
    type: "DERIVES",
    caption: "derives",
  },
];
