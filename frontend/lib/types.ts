/**
 * Hand-written TypeScript types aligned 1:1 with backend Pydantic models
 * (tech-spec §17 + app/api/schemas.py).
 *
 * Decision (E6.2): types are maintained by hand rather than generated via
 * openapi-typescript, so the frontend builds without a live backend. Keep this
 * file in sync when backend schemas change; a drift will surface as runtime
 * mismatches or TS errors in call sites that consume these types.
 */

export type FactType = "fact" | "preference" | "episode";

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

export interface ChunkProvenance {
  chunk_id: string;
  snippet: string;
  doc_id: string;
}

export interface FactDetailResponse {
  id: string;
  text: string;
  type: FactType;
  confidence: number;
  is_latest: boolean;
  created_at: string;
  source_doc_id: string;
  provenance: ChunkProvenance[];
}

export interface FactHistoryEntry {
  id: string;
  text: string;
  type: FactType;
  is_latest: boolean;
  path_length: number;
}

export interface FactHistoryResponse {
  facts: FactHistoryEntry[];
}

export interface QueryRequest {
  text: string;
  type_filter?: FactType | null;
}

export interface FactUsed {
  id: string;
  text: string;
  source_doc_id: string;
}

export interface SubgraphNode {
  id: string;
  label: "Fact";
  properties: Record<string, unknown>;
}

export interface SubgraphRelationship {
  source: string;
  target: string;
  type: RelationType;
}

export interface Subgraph {
  nodes: SubgraphNode[];
  relationships: SubgraphRelationship[];
}

export interface QueryResponse {
  answer: string;
  facts_used: FactUsed[];
  cited_fact_ids: string[];
  subgraph: Subgraph;
}

export interface QueryHistoryEntry {
  id: string;
  text: string;
  created_at: string;
}

export interface QueryHistoryResponse {
  items: QueryHistoryEntry[];
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
    | "grouping"
    | "consolidation"
    | "relation_detection"
    | "reconciliation"
    | "done";
  event: string;
  payload: Record<string, unknown>;
}

export interface GetGraphParams {
  is_latest?: boolean;
  type?: FactType;
  doc_id?: string;
  limit?: number;
}

/** Entity or event graph views (`GET /graph/entities`, `/graph/events`). */
export interface GetEntityEventGraphParams {
  is_latest?: boolean;
  limit?: number;
}

/** Limit-only graph views (`GET /graph/participation`, `/graph/concepts`). */
export interface GetGraphLimitParams {
  limit?: number;
}
