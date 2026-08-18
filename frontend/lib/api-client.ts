/**
 * Central typed API client — the only place Frontend components should call
 * `fetch` against the backend (E6.2). Types: see `./types.ts`.
 */

import type {
  ConnectivityRuleListResponse,
  ContradictionListResponse,
  DocumentListResponse,
  DocumentRequest,
  DreamingRunRequest,
  GetEntityEventGraphParams,
  GetGraphLimitParams,
  GraphResponse,
  IdentityItem,
  IdentityListResponse,
  JobResponse,
  JudgeRunListResponse,
  NodeQueryRequest,
  NodeQueryResponse,
  QueryHistoryResponse,
  ReconcileResponse,
  UnlinkFacetResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = await response.text().catch(() => null);
    }
    throw new ApiError(
      response.status,
      `API ${init?.method ?? "GET"} ${path} failed with ${response.status}`,
      body,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function getApiBaseUrl(): string {
  return API_URL;
}

export function postDocuments(body: DocumentRequest): Promise<JobResponse> {
  return request<JobResponse>("/documents", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getDocuments(): Promise<DocumentListResponse> {
  return request<DocumentListResponse>("/documents");
}

export function postDreamingRun(
  body: DreamingRunRequest = {},
): Promise<JobResponse> {
  return request<JobResponse>("/dreaming/run", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getEntityGraph(
  params: GetEntityEventGraphParams = {},
): Promise<GraphResponse> {
  const search = new URLSearchParams();
  if (params.is_latest !== undefined) {
    search.set("is_latest", String(params.is_latest));
  }
  if (params.limit !== undefined) {
    search.set("limit", String(params.limit));
  }
  if (params.include_concepts) {
    search.set("include_concepts", "true");
  }
  const qs = search.toString();
  return request<GraphResponse>(`/graph/entities${qs ? `?${qs}` : ""}`);
}

export function getEventGraph(
  params: GetEntityEventGraphParams = {},
): Promise<GraphResponse> {
  const search = new URLSearchParams();
  if (params.is_latest !== undefined) {
    search.set("is_latest", String(params.is_latest));
  }
  if (params.limit !== undefined) {
    search.set("limit", String(params.limit));
  }
  if (params.include_concepts) {
    search.set("include_concepts", "true");
  }
  const qs = search.toString();
  return request<GraphResponse>(`/graph/events${qs ? `?${qs}` : ""}`);
}

export function getParticipationGraph(
  params: GetGraphLimitParams = {},
): Promise<GraphResponse> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) {
    search.set("limit", String(params.limit));
  }
  const qs = search.toString();
  return request<GraphResponse>(`/graph/participation${qs ? `?${qs}` : ""}`);
}

export function getConceptOverview(
  params: GetGraphLimitParams = {},
): Promise<GraphResponse> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) {
    search.set("limit", String(params.limit));
  }
  const qs = search.toString();
  return request<GraphResponse>(`/graph/concepts${qs ? `?${qs}` : ""}`);
}

export function getConceptNeighbors(conceptId: string): Promise<GraphResponse> {
  return request<GraphResponse>(
    `/graph/concepts/${encodeURIComponent(conceptId)}`,
  );
}

export function getIdentities(): Promise<IdentityListResponse> {
  return request<IdentityListResponse>("/graph/identities");
}

export function getIdentity(uri: string): Promise<IdentityItem> {
  return request<IdentityItem>(`/graph/identities/${encodeURIComponent(uri)}`);
}

export function postUnlinkFacet(
  uri: string,
  facetNodeId: string,
): Promise<UnlinkFacetResponse> {
  return request<UnlinkFacetResponse>(
    `/graph/identities/${encodeURIComponent(uri)}/unlink`,
    {
      method: "POST",
      body: JSON.stringify({ facet_node_id: facetNodeId }),
    },
  );
}

export function getContradictions(): Promise<ContradictionListResponse> {
  return request<ContradictionListResponse>("/graph/contradictions");
}

export function getConnectivityRules(): Promise<ConnectivityRuleListResponse> {
  return request<ConnectivityRuleListResponse>("/graph/connectivity-rules");
}

export function getJudgeRuns(): Promise<JudgeRunListResponse> {
  return request<JudgeRunListResponse>("/graph/judge-runs");
}

export async function resetKnowledgeBase(): Promise<void> {
  await request<{ deleted: boolean }>("/graph", { method: "DELETE" });
}

export function postNodeQuery(body: NodeQueryRequest): Promise<NodeQueryResponse> {
  return request<NodeQueryResponse>("/graph/query", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getNodeQueryHistory(limit = 20): Promise<QueryHistoryResponse> {
  const qs = new URLSearchParams({ limit: String(limit) });
  return request<QueryHistoryResponse>(`/graph/queries?${qs.toString()}`);
}

export function getNodeQueryLogDetail(id: string): Promise<NodeQueryResponse> {
  return request<NodeQueryResponse>(
    `/graph/queries/${encodeURIComponent(id)}`,
  );
}

export function postReconcile(): Promise<ReconcileResponse> {
  return request<ReconcileResponse>("/reconcile", { method: "POST" });
}

/** Build the SSE stream URL for a job (consumed via EventSource in E8). */
export function eventsStreamUrl(jobId: string): string {
  return `${API_URL}/events/stream?job_id=${encodeURIComponent(jobId)}`;
}
