import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CogneeHttpClient } from './dist/src/client.js';

test('source search uses the existing identity and preserves native SQL evidence', async () => {
  const original = globalThis.fetch;
  const calls = [];
  const result = {evidence: [{retrieval_method: 'sql', structured: {sql: 'SELECT 1', rows: [{n: 1}]}}]};
  globalThis.fetch = async (url, init) => { calls.push([url, init]); return Response.json(result); };
  try {
    const client = new CogneeHttpClient('http://localhost:8011', 'agent-key');
    assert.deepEqual(await client.searchSources({query: 'count', sourceHint: 'unknown warehouse'}), result);
    assert.equal(calls.length, 1);
    assert.equal(calls[0][1].headers['X-Api-Key'], 'agent-key');
    assert.equal(JSON.parse(calls[0][1].body).source_hint, 'unknown warehouse');
    assert.equal(calls[0][1].redirect, 'error');
  } finally { globalThis.fetch = original; }
});
