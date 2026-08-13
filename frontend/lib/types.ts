/**
 * Hand-written TypeScript types aligned 1:1 with backend Pydantic models
 * (tech-spec §17 + app/api/schemas.py).
 *
 * Decision (E6.2): types are maintained by hand rather than generated via
 * openapi-typescript, so the frontend builds without a live backend. Keep this
 * file in sync when backend schemas change; a drift will surface as runtime
 * mismatches or TS errors in call sites that consume these types.
 */

export type RelationType = "updates" | "extends" | "derives";

export interface JobResponse {
  job_id: string;
}

export interface DocumentRequest {
  doc_id: string;
  text: string;
}

export interface DocumentSummary {
  doc_id: string;
  chunk_count: number;
  fact_count: number;
  first_ingested_at: string;
  last_ingested_at: string;
}

export interface DocumentListResponse {
  documents: DocumentSummary[];
}

export interface DreamingRunRequest {
  job_id?: string | null;
  doc_id?: string | null;
}

export interface GraphNode {
  id: string;
  caption: string;
  size?: number;
  color?: string;
  properties: Record<string, unknown>;
}

export interface GraphRelationship {
  id: string;
  from: string;
  to: string;
  type: string;
  caption?: string | null;
}

export interface GraphResponse {
  nodes: GraphNode[];
  relationships: GraphRelationship[];
}

export interface QueryHistoryEntry {
  id: string;
  text: string;
  created_at: string;
}

export interface QueryHistoryResponse {
  items: QueryHistoryEntry[];
}

export interface NodeQueryRequest {
  text: string;
}

export interface NodeUsed {
  id: string;
  name: string;
  type: "entity" | "event";
  source_doc_ids: string[];
}

export interface ConceptUsed {
  id: string;
  name: string;
}

export interface NodeSubgraphNode {
  id: string;
  label: "Node" | "Concept";
  properties: Record<string, unknown>;
}

export interface NodeSubgraphRelationship {
  source: string;
  target: string;
  type: string;
}

export interface NodeSubgraph {
  nodes: NodeSubgraphNode[];
  relationships: NodeSubgraphRelationship[];
}

export interface NodeQueryResponse {
  answer: string;
  nodes_used: NodeUsed[];
  concepts_used: ConceptUsed[];
  cited_node_ids: string[];
  subgraph: NodeSubgraph;
}

export interface ReconcileResponse {
  drift_count: number;
}

/** Pipeline SSE event (tech-spec §10). */
export interface PipelineEvent {
  ts: string;
  job_id: string;
  stage:
    | "chunking"
    | "extraction"
    | "node_extraction"
    | "grouping"
    | "consolidation"
    | "relation_detection"
    | "entity_resolution"
    | "entity_relation_classification"
    | "event_resolution_and_classification"
    | "reconciliation"
    | "done"
    | "failed";
  event: string;
  payload: Record<string, unknown>;
}

/** Entity or event graph views (`GET /graph/entities`, `/graph/events`). */
export interface GetEntityEventGraphParams {
  is_latest?: boolean;
  limit?: number;
  include_concepts?: boolean;
}

/** Limit-only graph views (`GET /graph/participation`, `/graph/concepts`). */
export interface GetGraphLimitParams {
  limit?: number;
}
