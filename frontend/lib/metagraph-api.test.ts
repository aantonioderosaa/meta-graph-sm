import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getConnectivityRules,
  getContradictions,
  getIdentities,
  getIdentity,
  getJudgeRuns,
  postUnlinkFacet,
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

  it("getIdentities hits /graph/identities", async () => {
    await getIdentities();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/identities",
      expect.any(Object),
    );
  });

  it("getIdentity encodes the uri", async () => {
    await getIdentity("identity:alice:Agente");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/identities/identity%3Aalice%3AAgente",
      expect.any(Object),
    );
  });

  it("postUnlinkFacet POSTs facet_node_id to /unlink", async () => {
    fetchMock.mockResolvedValue(
      okResponse({
        unlinked: true,
        identity_uri: "identity:alice:Agente",
        facet_node_id: "alice-ceo",
      }),
    );
    await postUnlinkFacet("identity:alice:Agente", "alice-ceo");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/graph/identities/identity%3Aalice%3AAgente/unlink",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ facet_node_id: "alice-ceo" }),
      }),
    );
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
});
