/**
 * Fixture covering entity / event / concept encoding plus Node relation types.
 * Used by unit tests — not imported by live graph panels.
 */

import type { GraphNode, GraphRelationship } from "./types";

export const FIXTURE_NODES: GraphNode[] = [
  {
    id: "entity-alice",
    caption: "Alice",
    properties: { type: "entity" },
  },
  {
    id: "entity-acme",
    caption: "Acme",
    properties: { type: "entity" },
  },
  {
    id: "event-summit",
    caption: "Summit 2024",
    properties: { type: "event" },
  },
  {
    id: "event-kickoff",
    caption: "Kickoff",
    properties: { type: "event" },
  },
  {
    id: "concept-remote",
    caption: "Remote work",
    properties: { type: "concept" },
  },
];

export const FIXTURE_RELATIONSHIPS: GraphRelationship[] = [
  {
    id: "rel-updates",
    from: "entity-alice",
    to: "entity-acme",
    type: "UPDATES",
    caption: "updates",
  },
  {
    id: "rel-extends",
    from: "entity-alice",
    to: "entity-acme",
    type: "EXTENDS",
    caption: "extends",
  },
  {
    id: "rel-precedes",
    from: "event-kickoff",
    to: "event-summit",
    type: "PRECEDES",
    caption: "precedes",
  },
  {
    id: "rel-causes",
    from: "event-kickoff",
    to: "event-summit",
    type: "CAUSES",
    caption: "causes",
  },
  {
    id: "rel-cooccurs",
    from: "event-kickoff",
    to: "event-summit",
    type: "COOCCURS",
    caption: "cooccurs",
  },
  {
    id: "rel-participates",
    from: "entity-alice",
    to: "event-summit",
    type: "PARTICIPATES",
    caption: "participates",
  },
  {
    id: "rel-has-concept",
    from: "entity-alice",
    to: "concept-remote",
    type: "HAS_CONCEPT",
    caption: "has_concept",
  },
];
