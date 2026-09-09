/**
 * Isolated event-graph HTTP client. Calls only `/event-graph/*`.
 */

import type {
  ArcoDettaglio,
  EventGraphCatalog,
  EventGraphHealthResponse,
  EventGraphJobResponse,
  EventGraphResponse,
  EventGraphStats,
  EventNlQueryResponse,
  EventQuerySpec,
  EventStructuredQueryResponse,
  GraphFilters,
  NodoDettaglio,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class EventGraphApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown = null) {
    super(message);
    this.name = "EventGraphApiError";
    this.status = status;
    this.body = body;
  }
}

export class EventGraphNetworkError extends Error {
  readonly path: string;
  readonly method: string;

  constructor(path: string, method = "GET") {
    super(`${method} ${path}: richiesta di rete non completata`);
    this.name = "EventGraphNetworkError";
    this.path = path;
    this.method = method;
  }
}

function queryString(params?: Record<string, string | undefined>): string {
  if (!params) return "";
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value != null && value !== "") {
      usp.set(key, value);
    }
  }
  const qs = usp.toString();
  return qs ? `?${qs}` : "";
}

export function eventGraphPath(
  suffix: string,
  params?: Record<string, string | undefined>,
): string {
  const path = suffix.startsWith("/") ? suffix : `/${suffix}`;
  return `/event-graph${path}${queryString(params)}`;
}

export function eventGraphUrl(
  suffix: string,
  params?: Record<string, string | undefined>,
): string {
  return `${API_URL}${eventGraphPath(suffix, params)}`;
}

export function eventGraphStreamUrl(jobId: string): string {
  return eventGraphUrl("/stream", { job_id: jobId });
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
    throw new EventGraphNetworkError(path, method);
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = await response.text().catch(() => null);
    }
    throw new EventGraphApiError(
      response.status,
      `API ${method} ${path} failed with ${response.status}`,
      body,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function fetchGraph(filters: GraphFilters = {}): Promise<EventGraphResponse> {
  return request<EventGraphResponse>(
    eventGraphPath("/graph", {
      documento: filters.documento,
      piano: filters.piano,
      lemma: filters.lemma,
    }),
  );
}

export function ingestDocument(
  docId: string,
  text: string,
): Promise<EventGraphJobResponse> {
  return request<EventGraphJobResponse>(eventGraphPath("/documents"), {
    method: "POST",
    body: JSON.stringify({ doc_id: docId, text }),
  });
}

export function fetchHealth(): Promise<EventGraphHealthResponse> {
  return request<EventGraphHealthResponse>(eventGraphPath("/health"));
}

function omitEmptyFields(value: unknown): unknown {
  if (value == null) return undefined;
  if (typeof value === "string") return value === "" ? undefined : value;
  if (Array.isArray(value)) return value;
  if (typeof value !== "object") return value;
  const out: Record<string, unknown> = {};
  for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
    const cleaned = omitEmptyFields(nested);
    if (cleaned !== undefined) out[key] = cleaned;
  }
  return Object.keys(out).length === 0 ? undefined : out;
}

export function queryStructured(
  spec: EventQuerySpec,
): Promise<EventStructuredQueryResponse> {
  const body = omitEmptyFields(spec) ?? {};
  return request<EventStructuredQueryResponse>(eventGraphPath("/query/structured"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function queryNl(testo: string): Promise<EventNlQueryResponse> {
  return request<EventNlQueryResponse>(eventGraphPath("/query/nl"), {
    method: "POST",
    body: JSON.stringify({ testo }),
  });
}

export function fetchCatalog(): Promise<EventGraphCatalog> {
  return request<EventGraphCatalog>(eventGraphPath("/catalog"));
}

export function fetchStats(): Promise<EventGraphStats> {
  return request<EventGraphStats>(eventGraphPath("/stats"));
}

/** Node dashboard: raw properties plus optional live `catena` for `:Evento`. */
export function fetchNodoDettaglio(id: string): Promise<NodoDettaglio> {
  return request<NodoDettaglio>(eventGraphPath(`/nodo/${encodeURIComponent(id)}`));
}

export function fetchArcoDettaglio(id: string): Promise<ArcoDettaglio> {
  return request<ArcoDettaglio>(eventGraphPath(`/arco/${encodeURIComponent(id)}`));
}
