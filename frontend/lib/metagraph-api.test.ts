import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  getConnectivityRules,
  getContradictions,
  getEventIncompleteness,
  getJudgeRuns,
  getNodeMetadata,
  NetworkError,
  userFacingApiError,
} from "./api-client";

function okResponse(body: unknown = { items: [] }) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

describe("metagraph layer API client paths", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(okResponse());
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getContradictions hits /graph/contradictions", async () => {
    await getContradictions();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/contradictions",
      expect.any(Object),
    );
  });

  it("getConnectivityRules hits /graph/connectivity-rules", async () => {
    await getConnectivityRules();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/connectivity-rules",
      expect.any(Object),
    );
  });

  it("getJudgeRuns hits /graph/judge-runs", async () => {
    await getJudgeRuns();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/judge-runs",
      expect.any(Object),
    );
  });

  it("getEventIncompleteness hits /graph/event-incompleteness", async () => {
    await getEventIncompleteness();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/event-incompleteness",
      expect.any(Object),
    );
  });

  it("wraps fetch failures as NetworkError with the path", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(getContradictions()).rejects.toBeInstanceOf(NetworkError);
    await expect(getContradictions()).rejects.toMatchObject({
      path: "/graph/contradictions",
      message: "GET /graph/contradictions: richiesta di rete non completata",
    });
    await expect(getNodeMetadata("alice")).rejects.toMatchObject({
      path: "/graph/metadata/alice",
    });
  });

  it("userFacingApiError distinguishes HTTP vs network vs Failed to fetch", () => {
    expect(userFacingApiError(new ApiError(404, "nope"), "Metadati")).toBe(
      "Metadati non disponibili (404)",
    );
    expect(
      userFacingApiError(new NetworkError("/graph/contradictions"), "Contraddizioni"),
    ).toBe(
      "Contraddizioni — GET /graph/contradictions: richiesta di rete non completata",
    );
    expect(userFacingApiError(new TypeError("Failed to fetch"), "Metadati")).toBe(
      "Metadati: richiesta di rete fallita",
    );
  });
});
