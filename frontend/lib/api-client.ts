/**
 * Central typed API client — the only place Frontend components should call
 * `fetch` against the backend (E6.2). Types: see `./types.ts`.
 */

import type {
  DocumentListResponse,
  DocumentRequest,
  DreamingRunRequest,
  FactDetailResponse,
  FactHistoryResponse,
  GetEntityEventGraphParams,
  GetGraphLimitParams,
  GetGraphParams,
  GraphResponse,
  JobResponse,
  QueryHistoryResponse,
  QueryRequest,
  QueryResponse,
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

export function getGraph(params: GetGraphParams = {}): Promise<GraphResponse> {
  const search = new URLSearchParams();
  if (params.is_latest !== undefined) {
    search.set("is_latest", String(params.is_latest));
  }
  if (params.type !== undefined) {
    search.set("type", params.type);
  }
  if (params.doc_id !== undefined) {
    search.set("doc_id", params.doc_id);
  }
  if (params.limit !== undefined) {
    search.set("limit", String(params.limit));
  }
  const qs = search.toString();
  return request<GraphResponse>(`/graph${qs ? `?${qs}` : ""}`);
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

export async function resetKnowledgeBase(): Promise<void> {
  await request<{ deleted: boolean }>("/graph", { method: "DELETE" });
}

export function getFact(id: string): Promise<FactDetailResponse> {
  return request<FactDetailResponse>(`/facts/${encodeURIComponent(id)}`);
}

export function getFactHistory(id: string): Promise<FactHistoryResponse> {
  return request<FactHistoryResponse>(
    `/facts/${encodeURIComponent(id)}/history`,
  );
}

export function postQuery(body: QueryRequest): Promise<QueryResponse> {
  return request<QueryResponse>("/query", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getQueryHistory(limit = 20): Promise<QueryHistoryResponse> {
  const qs = new URLSearchParams({ limit: String(limit) });
  return request<QueryHistoryResponse>(`/queries?${qs.toString()}`);
}

export function getQueryLogDetail(id: string): Promise<QueryResponse> {
  return request<QueryResponse>(`/queries/${encodeURIComponent(id)}`);
}

export function postReconcile(): Promise<ReconcileResponse> {
  return request<ReconcileResponse>("/reconcile", { method: "POST" });
}

/** Build the SSE stream URL for a job (consumed via EventSource in E8). */
export function eventsStreamUrl(jobId: string): string {
  return `${API_URL}/events/stream?job_id=${encodeURIComponent(jobId)}`;
}
