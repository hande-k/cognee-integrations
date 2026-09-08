import { CogneeHttpClient } from "../../src/client";
import { createSourceSearchTool } from "../../src/tools";

test("source tool preserves native SQL provenance and arbitrary source names", async () => {
  const response = { evidence: [{retrieval_method: "sql", structured: {sql: "SELECT 1", rows: [{n: 1}]}}], coverage: {complete: false} };
  const searchSources = jest.fn().mockResolvedValue(response);
  const result = await createSourceSearchTool({searchSources}).execute("call", {query: "counts", source: "custom warehouse"});
  expect(result.details).toEqual(response);
  expect(searchSources).toHaveBeenCalledWith({query: "counts", sourceHint: "custom warehouse", datasetIds: undefined, includeConnections: undefined});
});

test("source transport sends the agent credential and does not fall back on denial", async () => {
  const previous = global.fetch;
  const fetchMock = jest.fn().mockResolvedValue(new Response("denied", {status: 403}));
  global.fetch = fetchMock;
  try {
    const client = new CogneeHttpClient("http://localhost:8011", "agent-key");
    await expect(client.searchSources({query: "counts"})).rejects.toThrow("403");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8011/api/v1/datasets/source-search");
    expect(fetchMock.mock.calls[0][1].headers["X-Api-Key"]).toBe("agent-key");
    expect(fetchMock.mock.calls[0][1].redirect).toBe("error");
  } finally { global.fetch = previous; }
});
