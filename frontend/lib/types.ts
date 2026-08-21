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
  node_count: number;
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

export interface BundleRelation {
  id: string;
  from: string;
  to: string;
  type: string;
  relation?: string | null;
  kernel_parent?: string | null;
  witnesses_a?: string[];
  witnesses_b?: string[];
  provenance?: unknown;
  valid_time?: string | null;
  system_time?: string | null;
  epistemic_status?: "asserted" | "derived";
}

export interface BundleResponse {
  items: BundleRelation[];
}

export interface MetadataBreadcrumbItem {
  id: string;
  name: string;
  kernel_category?: string | null;
}

export interface NodeMetadataResponse {
  id: string;
  kind: "concept" | "node" | string;
  name: string;
  kernel_category?: string | null;
  definition?: string | null;
  aliases?: string[];
  is_a_breadcrumb?: MetadataBreadcrumbItem[];
  member_count?: number | null;
  summary?: string | null;
  attributes?: Record<string, unknown>;
  identity_uris?: string[];
  node_type?: "entity" | "event" | string | null;
}

export interface DomainListItem {
  id: string;
  name: string;
  kernel_category?: string | null;
  definition?: string | null;
  promoted: boolean;
  direct_member_count: number;
}

export interface DomainListResponse {
  items: DomainListItem[];
}

export interface DomainDictionaryItem {
  kind: "relation" | "attribute" | string;
  name: string;
  kernel_parent?: string | null;
  count: number;
}

export interface DomainDictionaryResponse {
  items: DomainDictionaryItem[];
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

export interface DerivationStep {
  kind: "s0" | "s1";
  detail: string;
}

export interface QueryCitation {
  id: string;
  epistemic_status: "asserted" | "derived";
  derivation_chain?: DerivationStep[] | null;
}

export interface NodeQueryResponse {
  answer: string;
  nodes_used: NodeUsed[];
  concepts_used: ConceptUsed[];
  cited_node_ids: string[];
  subgraph: NodeSubgraph;
  citations?: QueryCitation[];
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
    | "node_extraction"
    | "entity_resolution"
    | "backbone_classification"
    | "promote_clusters"
    | "entity_relation_classification"
    | "event_resolution_and_classification"
    | "reconciliation"
    | "judge"
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

export interface IdentityFacet {
  id: string;
  name: string;
  kernel_category?: string | null;
}

export interface IdentityItem {
  uri: string;
  facets: IdentityFacet[];
}

export interface IdentityListResponse {
  items: IdentityItem[];
}

export interface UnlinkFacetRequest {
  facet_node_id: string;
}

export interface UnlinkFacetResponse {
  unlinked: boolean;
  identity_uri: string;
  facet_node_id: string;
}

export interface ContradictionItem {
  id: string;
  left_id: string;
  left_name: string;
  right_id: string;
  right_name: string;
  subject_id?: string | null;
}

export interface ContradictionListResponse {
  items: ContradictionItem[];
}

export interface ConnectivityRuleItem {
  source_category: string;
  relation_type: string;
  target_category: string;
  generalization_level: number;
  origin_count: number;
}

export interface ConnectivityRuleListResponse {
  items: ConnectivityRuleItem[];
}

export interface JudgeRunItem {
  id: string;
  batch_id?: string | null;
  timestamp?: string | null;
  anti_blur: number;
  equivalent_to: number;
  reraffine: number;
  identity: number;
  missed_contradictions: number;
  temporal: number;
}

export interface JudgeRunListResponse {
  items: JudgeRunItem[];
}

export interface EventIncompletenessItem {
  event_id: string;
  text: string;
  missing_context?: string | null;
  first_seen_run_id?: string | null;
  checks_without_progress: number;
  incomplete_at?: string | null;
  timestamp?: string | null;
}

export interface EventIncompletenessListResponse {
  items: EventIncompletenessItem[];
}
