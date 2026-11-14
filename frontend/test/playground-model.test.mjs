/**
 * The playground's receipt rules.
 *
 * Each case here is a sentence the panel would print wrong without anyone
 * noticing: a timeout reported as "no results", a masked key that is not
 * actually masked, a scan given a generation time it does not have.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  costLine,
  endpointLabel,
  endpointHost,
  errorMessage,
  errorReason,
  keyFamily,
  keySuggestion,
  maskKey,
  resultRoute,
  scanPayload,
  scanReadiness,
  toolOutcome,
  toolQuery,
  toolSummary,
  usageLine,
} from '../src/verify/playgroundModel.js';

test('a key is masked to a prefix and four characters', () => {
  assert.equal(maskKey('sk-live-abcdefghijkl9f2c'), 'sk-liv…9f2c');
  assert.equal(maskKey(''), 'not set');
  assert.equal(maskKey('   '), 'not set');
  assert.equal(maskKey('short'), 'sh…');
});

test('a key never appears whole in its own label', () => {
  const key = 'sk-ant-api03-verylongsecretvalue';
  assert.equal(maskKey(key).includes(key), false);
});

test('an endpoint is labelled without the scheme', () => {
  assert.equal(endpointLabel('https://api.mistral.ai/v1'), 'api.mistral.ai/v1');
  assert.equal(endpointLabel('http://127.0.0.1:11434/v1'), '127.0.0.1:11434/v1');
  assert.equal(endpointLabel(''), 'no endpoint');
});

test('usage falls back to a sentence rather than a pair of zeros', () => {
  assert.equal(usageLine({ input_tokens: 320, output_tokens: 48 }), '320 in · 48 out');
  assert.equal(usageLine({}), 'usage not reported');
  assert.equal(usageLine(null), 'usage not reported');
});

test('an empty search is not a failed search', () => {
  assert.deepEqual(toolOutcome({ ok: true, results: 4 }), { tone: 'ok', label: '4 results' });
  assert.deepEqual(toolOutcome({ ok: true, results: 1 }), { tone: 'ok', label: '1 result' });
  assert.deepEqual(toolOutcome({ ok: true, results: 0 }), { tone: 'warn', label: 'no results' });
});

test('a failed call reports the reason it gave, not a zero', () => {
  assert.deepEqual(toolOutcome({ ok: false, results: 0, error: 'Tavily timeout' }), {
    tone: 'danger',
    label: 'Tavily timeout',
  });
  assert.deepEqual(toolOutcome({ ok: false }), { tone: 'danger', label: 'failed' });
});

test('the query is read from arguments whether they arrived as an object or text', () => {
  assert.equal(toolQuery({ arguments: { query: 'acme pro price' } }), 'acme pro price');
  assert.equal(toolQuery({ arguments: '{"query": broken' }), '{"query": broken');
  assert.equal(toolQuery({}), '');
});

test('a run with tools off says so instead of claiming zero searches', () => {
  assert.match(
    toolSummary({ tools_enabled: false, tools_available: true }),
    /used no live sources/,
  );
  assert.match(
    toolSummary({ tools_enabled: false, tools_available: false }),
    /no search key/,
  );
});

test('a run with tools on and no calls says the model asked for none', () => {
  assert.match(
    toolSummary({ tools_enabled: true, tool_calls: [] }),
    /asked for none/,
  );
});

test('the tool summary counts what the model actually saw', () => {
  const line = toolSummary({
    tools_enabled: true,
    tool_calls: [
      { ok: true, results: 4 },
      { ok: true, results: 3 },
      { ok: false, results: 0, error: 'no such tool' },
    ],
  });
  assert.equal(line, '2 searches · 7 results seen · 1 failed');
});

test('a scan carries the answer verbatim and the model generation time', () => {
  const payload = scanPayload(
    { answer: '  Acme costs $99.  ', generated_at: '2026-11-13T10:00:00Z' },
    'What does Acme cost?',
  );
  assert.deepEqual(payload, {
    answer: 'Acme costs $99.',
    prompt: 'What does Acme cost?',
    generated_at: '2026-11-13T10:00:00Z',
    generated_at_source: 'user_supplied',
    skip_non_factual: true,
    model: '',
    provider: '',
  });
});

test('a scan records the model that answered, not the one requested', () => {
  const payload = scanPayload(
    {
      answer: 'Acme costs $99.',
      model: 'mistral/mistral-small-latest',
      resolved_model: 'mistral-small-latest',
      provider: 'mistral',
    },
    'q',
  );
  assert.equal(payload.model, 'mistral-small-latest');
  assert.equal(payload.provider, 'mistral');
});

test('a bring-your-own endpoint has only the typed model to record', () => {
  const payload = scanPayload(
    { answer: 'x', model: 'local/mixtral-8x7b', resolved_model: '', provider: '' },
    'q',
  );
  assert.equal(payload.model, 'local/mixtral-8x7b');
  assert.equal(payload.provider, '');
});

test('a receipt with no timestamp produces an unknown generation time, not now', () => {
  const payload = scanPayload({ answer: 'x' }, 'q');
  assert.equal(payload.generated_at, null);
  assert.equal(payload.generated_at_source, 'unknown');
});

test('an empty answer is refused before the API has to refuse it', () => {
  assert.equal(scanReadiness(null).ready, false);
  assert.equal(scanReadiness({ answer: '   ' }).ready, false);
  assert.equal(scanReadiness({ answer: 'text' }).ready, true);
});

/* --- naming the provider that refused the key ------------------------- --
 * A 401 is only useful if the page can say which console the key came from.
 * These assertions are about refusing to guess: an opaque key returns null,
 * because sending someone to the wrong console is worse than saying nothing.
 */

test('only a key with a provider signature is named', () => {
  assert.equal(keyFamily('gsk_abc123').id, 'groq');
  assert.equal(keyFamily('sk-or-v1-deadbeef').id, 'openrouter');
  assert.equal(keyFamily('sk-ant-api03-xyz').id, 'anthropic');
  assert.equal(keyFamily('AIzaSyA1234567890abcdef').id, 'gemini');
  assert.equal(keyFamily('tgp_v1_abc').id, 'together');
  assert.equal(keyFamily('tvly-dev-123').id, 'tavily');
});

test('an opaque key is not attributed to anybody', () => {
  assert.equal(keyFamily(''), null);
  assert.equal(keyFamily('   '), null);
  assert.equal(keyFamily('sk-1234567890abcdefghij'), null);
  assert.equal(keyFamily('9f2c8a7b6d5e4f3a2b1c0d9e8f7a6b5c'), null);
});

test('a groq key pointed at mistral is reported as a mismatch', () => {
  const presets = [
    { id: 'mistral', base_url: 'https://api.mistral.ai/v1' },
    { id: 'groq', base_url: 'https://api.groq.com/openai/v1' },
  ];
  const hint = keySuggestion('gsk_abc', presets, 'https://api.mistral.ai/v1');
  assert.equal(hint.family.label, 'Groq');
  assert.equal(hint.preset.id, 'groq');
  assert.equal(hint.matchesEndpoint, false);
});

test('the same key pointed at its own endpoint is a match, not a mismatch', () => {
  const presets = [{ id: 'groq', base_url: 'https://api.groq.com/openai/v1' }];
  const hint = keySuggestion('gsk_abc', presets, 'https://api.groq.com/openai/v1');
  assert.equal(hint.matchesEndpoint, true);
});

test('a key this client cannot call has no preset to offer', () => {
  const presets = [{ id: 'mistral', base_url: 'https://api.mistral.ai/v1' }];
  const hint = keySuggestion('sk-ant-api03-xyz', presets, 'https://api.mistral.ai/v1');
  assert.equal(hint.preset, null);
  assert.equal(hint.matchesEndpoint, false);
});

test('an endpoint host is named even when the URL is not parseable', () => {
  assert.equal(endpointHost('https://api.mistral.ai/v1'), 'api.mistral.ai');
  assert.equal(endpointHost('api.mistral.ai/v1'), 'api.mistral.ai');
  assert.equal(endpointHost(''), '');
});

test('a refusal is shown without its internal error code', () => {
  assert.equal(
    errorMessage({
      message: 'E_PROVIDER_FATAL: The endpoint refused that API key (HTTP 401).',
    }),
    'The endpoint refused that API key (HTTP 401).',
  );
  assert.equal(errorMessage({ payload: { error: { message: 'plain' } } }), 'plain');
  assert.equal(errorMessage(null), '');
});

test('the provider reason is read from details, not from the sentence', () => {
  assert.equal(errorReason({ payload: { error: { details: { reason: 'auth' } } } }), 'auth');
  assert.equal(errorReason({ payload: { error: {} } }), '');
  assert.equal(errorReason(null), '');
});

/* --- where the call actually went --------------------------------------- --
 * A gateway reply is a receipt, not an echo. Routing may serve the call with a
 * different model than the one requested, so the resolved name is what the page
 * must print. Repeating the request would hide exactly the fact a receipt is
 * for.
 */

test('a gateway receipt names the model that served the call', () => {
  const route = resultRoute({
    source: 'gateway',
    model: 'mistral/mistral-small-latest',
    resolved_model: 'mistral-small-latest',
    provider: 'mistral',
    endpoint: 'http://127.0.0.1:8080/v1',
  });
  assert.equal(route.gateway, true);
  assert.equal(route.model, 'mistral-small-latest');
  assert.equal(route.where, 'mistral · via the gateway');
});

test('a gateway receipt without a resolution keeps what was asked for', () => {
  const route = resultRoute({ source: 'gateway', model: 'm', provider: '' });
  assert.equal(route.model, 'm');
  assert.equal(route.where, 'via the gateway');
});

test('an endpoint receipt still names the host it called', () => {
  const route = resultRoute({ source: 'endpoint', model: 'm', endpoint: 'https://api.mistral.ai/v1' });
  assert.equal(route.gateway, false);
  assert.equal(route.model, 'm');
  assert.equal(route.where, 'api.mistral.ai/v1');
});

test('a result from before the source existed still renders', () => {
  const route = resultRoute({ model: 'm', endpoint: 'https://api.example/v1' });
  assert.equal(route.gateway, false);
  assert.equal(route.where, 'api.example/v1');
});

test('a cost is precise enough not to read as free', () => {
  assert.equal(costLine(0.0004), '$0.000400');
  assert.equal(costLine(0.123456), '$0.1235');
  assert.equal(costLine(0), '$0.00');
});

test('an unreported cost says so instead of inventing zero', () => {
  assert.equal(costLine(null), 'not reported');
  assert.equal(costLine(undefined), 'not reported');
  assert.equal(costLine(''), 'not reported');
  assert.equal(costLine('nonsense'), 'not reported');
});
