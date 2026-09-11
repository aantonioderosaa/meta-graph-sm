import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  API_URL,
  EventGraphApiError,
  eventGraphPath,
  eventGraphStreamUrl,
  eventGraphUrl,
  fetchArcoDettaglio,
  fetchCatalog,
  fetchGraph,
  fetchHealth,
  fetchNodoDettaglio,
  fetchStats,
  ingestDocument,
  queryNl,
  queryStructured,
} from "./api";

function okResponse(body: unknown = {}) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

describe("event-graph api paths", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(okResponse({ elements: { nodes: [], edges: [] } }));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("builds only /event-graph/* paths", () => {
    expect(eventGraphPath("/documents")).toBe("/event-graph/documents");
    expect(eventGraphPath("/stream", { job_id: "j1" })).toBe(
      "/event-graph/stream?job_id=j1",
    );
    expect(eventGraphPath("/health")).toBe("/event-graph/health");
    expect(eventGraphPath("/graph")).toBe("/event-graph/graph");
    expect(eventGraphUrl("/graph")).toBe(`${API_URL}/event-graph/graph`);
  });

  it("fetchGraph GETs /event-graph/graph", async () => {
    await fetchGraph();
    expect(fetchMock).toHaveBeenCalledWith(
      `${API_URL}/event-graph/graph`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
  });

  it("fetchGraph forwards optional filters", async () => {
    await fetchGraph({ documento: "doc-1", piano: "PRIMO_PIANO", lemma: "arrivare" });
    expect(fetchMock).toHaveBeenCalledWith(
      `${API_URL}/event-graph/graph?documento=doc-1&piano=PRIMO_PIANO&lemma=arrivare`,
      expect.any(Object),
    );
  });

  it("fetchGraph forwards vista=ordine when set", async () => {
    await fetchGraph({ vista: "ordine", documento: "doc-1" });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("vista=ordine");
    expect(url).toContain("documento=doc-1");
  });

  it("fetchGraph forwards vista=temporale when set", async () => {
    await fetchGraph({ vista: "temporale", documento: "doc-1" });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("vista=temporale");
    expect(url).toContain("documento=doc-1");
  });

  it("fetchGraph forwards vista=relazioni when set", async () => {
    await fetchGraph({ vista: "relazioni", documento: "doc-1" });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("vista=relazioni");
    expect(url).toContain("documento=doc-1");
  });

  it("fetchNodoDettaglio GETs /event-graph/nodo/:id", async () => {
    fetchMock.mockResolvedValue(okResponse({ id: "ev-1", labels: ["Evento"], proprieta: {} }));
    const result = await fetchNodoDettaglio("ev-1");
    expect(result.id).toBe("ev-1");
    expect(fetchMock).toHaveBeenCalledWith(
      `${API_URL}/event-graph/nodo/ev-1`,
      expect.any(Object),
    );
  });

  it("fetchArcoDettaglio GETs /event-graph/arco/:id and encodes the id", async () => {
    fetchMock.mockResolvedValue(
      okResponse({
        id: "PRECEDE|a|b",
        tipo: "PRECEDE",
        proprieta: {},
        source: { id: "a", labels: [], label: null },
        target: { id: "b", labels: [], label: null },
      }),
    );
    const result = await fetchArcoDettaglio("PRECEDE|a|b");
    expect(result.tipo).toBe("PRECEDE");
    expect(fetchMock).toHaveBeenCalledWith(
      `${API_URL}/event-graph/arco/${encodeURIComponent("PRECEDE|a|b")}`,
      expect.any(Object),
    );
  });

  it("ingestDocument POSTs /event-graph/documents", async () => {
    fetchMock.mockResolvedValue(okResponse({ job_id: "job-1" }));
    const result = await ingestDocument("doc-1", "testo");
    expect(result.job_id).toBe("job-1");
    expect(fetchMock).toHaveBeenCalledWith(
      `${API_URL}/event-graph/documents`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ doc_id: "doc-1", text: "testo" }),
      }),
    );
  });

  it("fetchHealth GETs /event-graph/health", async () => {
    fetchMock.mockResolvedValue(okResponse({ status: "ok" }));
    const health = await fetchHealth();
    expect(health.status).toBe("ok");
    expect(fetchMock).toHaveBeenCalledWith(
      `${API_URL}/event-graph/health`,
      expect.any(Object),
    );
  });

  it("eventGraphStreamUrl builds /event-graph/stream?job_id=", () => {
    expect(eventGraphStreamUrl("abc")).toBe(
      `${API_URL}/event-graph/stream?job_id=abc`,
    );
  });

  it("queryStructured POSTs /event-graph/query/structured and omits empty fields", async () => {
    fetchMock.mockResolvedValue(
      okResponse({
        id: "q1",
        modo: "structured",
        spec: { lemma: "arrivare" },
        risultato: { eventi: [], archi: [] },
      }),
    );
    await queryStructured({
      lemma: "arrivare",
      piano: undefined,
      fonte: "",
      finestra_tempo_assoluto: { da: "", a: "" },
    });
    expect(fetchMock).toHaveBeenCalledWith(
      `${API_URL}/event-graph/query/structured`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ lemma: "arrivare" }),
      }),
    );
  });

  it("fetchCatalog GETs /event-graph/catalog", async () => {
    fetchMock.mockResolvedValue(
      okResponse({ nodes: [], traits: {}, arches: {} }),
    );
    await fetchCatalog();
    expect(fetchMock).toHaveBeenCalledWith(
      `${API_URL}/event-graph/catalog`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
  });

  it("fetchStats GETs /event-graph/stats", async () => {
    fetchMock.mockResolvedValue(
      okResponse({ nodi: {}, archi: {}, tratti: { piano: {} } }),
    );
    await fetchStats();
    expect(fetchMock).toHaveBeenCalledWith(
      `${API_URL}/event-graph/stats`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
  });

  it("fetchStats throws EventGraphApiError on 503", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ detail: "neo4j down" }),
      text: async () => "neo4j down",
    });
    await expect(fetchStats()).rejects.toBeInstanceOf(EventGraphApiError);
    await expect(fetchStats()).rejects.toMatchObject({ status: 503 });
  });

  it("queryNl POSTs /event-graph/query/nl with {testo}", async () => {
    fetchMock.mockResolvedValue(
      okResponse({
        id: "q2",
        modo: "nl",
        spec_generata: { lemma: "piovere" },
        risultato: { eventi: [], archi: [] },
      }),
    );
    await queryNl("chi è arrivato?");
    expect(fetchMock).toHaveBeenCalledWith(
      `${API_URL}/event-graph/query/nl`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ testo: "chi è arrivato?" }),
      }),
    );
  });

  it("never targets legacy /documents or /events/stream", async () => {
    await fetchGraph();
    await ingestDocument("d", "t");
    await fetchHealth();
    await queryStructured({ lemma: "x" });
    await queryNl("y");
    await fetchCatalog();
    await fetchStats();
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.every((url) => url.includes("/event-graph/"))).toBe(true);
    expect(urls.some((url) => url.endsWith("/documents") && !url.includes("/event-graph/"))).toBe(
      false,
    );
    expect(urls.some((url) => url.includes("/graph/query"))).toBe(false);
    expect(eventGraphStreamUrl("x")).not.toContain("/events/stream");
  });
});
