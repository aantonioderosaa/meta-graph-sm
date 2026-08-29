/**
 * Central typed API client — the only place Frontend components should call
 * `fetch` against the backend (E6.2). Types: see `./types.ts`.
 */

import type {
  BundleResponse,
  ConnectivityRuleListResponse,
  ContradictionListResponse,
  DocumentListResponse,
  DocumentRequest,
  DomainDictionaryResponse,
  DomainListResponse,
  DreamingRunRequest,
  EventIncompletenessListResponse,
  GetEntityEventGraphParams,
  GetGraphLimitParams,
  GraphResponse,
  JobResponse,
  JudgeRunListResponse,
  NodeMetadataResponse,
  NodeQueryRequest,
  NodeQueryResponse,
  QueryHistoryResponse,
  ReconcileResponse,
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

/** Thrown when ``fetch()`` itself fails (backend down, CORS, mixed content). */
export class NetworkError extends Error {
  readonly path: string;
  readonly method: string;

  constructor(path: string, method = "GET") {
    super(`${method} ${path}: richiesta di rete non completata`);
    this.name = "NetworkError";
    this.path = path;
    this.method = method;
  }
}

export function userFacingApiError(err: unknown, resource: string): string {
  if (err instanceof ApiError) {
    return `${resource} non disponibili (${err.status})`;
  }
  if (err instanceof NetworkError) {
    return `${resource} — ${err.message}`;
  }
  const message = err instanceof Error ? err.message : "";
  if (!message || /failed to fetch/i.test(message)) {
    return `${resource}: richiesta di rete fallita`;
  }
  return `${resource}: ${message}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new NetworkError(path, method);
  }

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

export function getMacroGraph(
  params: GetGraphLimitParams = {},
): Promise<GraphResponse> {
  const search = new URLSearchParams();
  if (params.limit !== undefined) {
    search.set("limit", String(params.limit));
  }
  const qs = search.toString();
  return request<GraphResponse>(`/graph/macro${qs ? `?${qs}` : ""}`);
}

export function getDomains(): Promise<DomainListResponse> {
  return request<DomainListResponse>("/graph/domains");
}

export function getDomainsGraph(): Promise<GraphResponse> {
  return request<GraphResponse>("/graph/domains-graph");
}

export function getDomainDictionary(
  conceptId: string,
): Promise<DomainDictionaryResponse> {
  return request<DomainDictionaryResponse>(
    `/graph/domains/${encodeURIComponent(conceptId)}/dictionary`,
  );
}

export function getDomainRules(
  conceptId: string,
): Promise<ConnectivityRuleListResponse> {
  return request<ConnectivityRuleListResponse>(
    `/graph/domains/${encodeURIComponent(conceptId)}/rules`,
  );
}

export function getDomainChildrenGraph(conceptId: string): Promise<GraphResponse> {
  return request<GraphResponse>(
    `/graph/domains/${encodeURIComponent(conceptId)}/children-graph`,
  );
}

export function getGraphBundle(
  nodeAId: string,
  nodeBId: string,
): Promise<BundleResponse> {
  return request<BundleResponse>(
    `/graph/bundle/${encodeURIComponent(nodeAId)}/${encodeURIComponent(nodeBId)}`,
  );
}

export function getNodeMetadata(nodeId: string): Promise<NodeMetadataResponse> {
  return request<NodeMetadataResponse>(
    `/graph/metadata/${encodeURIComponent(nodeId)}`,
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

export function getEventIncompleteness(): Promise<EventIncompletenessListResponse> {
  return request<EventIncompletenessListResponse>("/graph/event-incompleteness");
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
