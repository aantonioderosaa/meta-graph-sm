import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getConceptNeighbors,
  getConceptOverview,
  getEntityGraph,
  getEventGraph,
  getParticipationGraph,
} from "./api-client";

const emptyGraph = { nodes: [], relationships: [] };

function okResponse(body: unknown = emptyGraph) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

describe("entity/event/concept API client paths", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(okResponse());
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getEntityGraph hits /graph/entities with query params", async () => {
    await getEntityGraph({ is_latest: true, limit: 50 });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/entities?is_latest=true&limit=50",
      expect.any(Object),
    );
  });

  it("getEventGraph hits /graph/events with query params", async () => {
    await getEventGraph({ is_latest: false });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/events?is_latest=false",
      expect.any(Object),
    );
  });

  it("getEntityGraph sends include_concepts when true", async () => {
    await getEntityGraph({ is_latest: true, include_concepts: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/entities?is_latest=true&include_concepts=true",
      expect.any(Object),
    );
  });

  it("getEventGraph sends include_concepts when true", async () => {
    await getEventGraph({ include_concepts: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/events?include_concepts=true",
      expect.any(Object),
    );
  });

  it("getParticipationGraph hits /graph/participation", async () => {
    await getParticipationGraph({ limit: 10 });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/participation?limit=10",
      expect.any(Object),
    );
  });

  it("getConceptOverview hits /graph/concepts", async () => {
    await getConceptOverview();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/concepts",
      expect.any(Object),
    );
  });

  it("getConceptNeighbors encodes the concept id in the path", async () => {
    await getConceptNeighbors("c/1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/concepts/c%2F1",
      expect.any(Object),
    );
  });
});
