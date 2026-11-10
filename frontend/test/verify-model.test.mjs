/**
 * The presentation rules that are easy to break without an error.
 *
 * Every case here is something a reviewer would only notice by reading: an
 * invented fifth verdict, a highlight that points two characters off, a run
 * called live after it finished. They are asserted here because none of them
 * throws.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  STATUS,
  VERDICTS,
  buildSegments,
  claimCounts,
  describeEvent,
  formatCost,
  gatewayRoute,
  isLive,
  isProblem,
  reasonLabel,
  shortDate,
  shortUrl,
  statusMeta,
} from '../src/verify/model.js';

test('there are exactly four verdicts, and pending is not one of them', () => {
  assert.deepEqual(VERDICTS, ['supported', 'changed', 'contradicted', 'unverifiable']);
  assert.deepEqual(Object.keys(STATUS).sort(), [
    'changed',
    'contradicted',
    'pending',
    'supported',
    'unverifiable',
  ]);
  assert.equal(VERDICTS.includes('pending'), false);
  assert.equal(VERDICTS.includes('partly_true'), false);
});

test('an unknown status is shown as pending rather than as a new verdict', () => {
  assert.equal(statusMeta('mostly_true').label, 'Pending');
  assert.equal(statusMeta(undefined).label, 'Pending');
  assert.equal(statusMeta('changed').label, 'Changed');
});

test('changed is not coloured as success', () => {
  assert.notEqual(STATUS.changed.tone, STATUS.supported.tone);
  assert.equal(STATUS.changed.tone, 'info');
});

test('counts include abstentions and unknown statuses never inflate a verdict', () => {
  const counts = claimCounts([
    { status: 'supported' },
    { status: 'changed' },
    { status: 'unverifiable' },
    { status: 'unknown-status' },
    {},
  ]);
  assert.deepEqual(counts, {
    supported: 1,
    changed: 1,
    contradicted: 0,
    unverifiable: 1,
    pending: 2,
    total: 5,
  });
});

test('segments reassemble the answer exactly', () => {
  const answer = 'Acme Pro costs $99 per month. Acme is in Berlin.';
  const first = 'Acme Pro costs $99 per month.';
  const second = 'Acme is in Berlin.';
  const claims = [
    { id: 'a', span_start: 0, span_end: first.length, status: 'supported' },
    {
      id: 'b',
      span_start: answer.indexOf(second),
      span_end: answer.indexOf(second) + second.length,
      status: 'unverifiable',
    },
  ];
  const segments = buildSegments(answer, claims);
  assert.equal(segments.map((segment) => segment.text).join(''), answer);
  assert.deepEqual(
    segments.filter((segment) => segment.claim).map((segment) => segment.claim.id),
    ['a', 'b'],
  );
});

test('a stale offset is dropped, never clamped onto other words', () => {
  const answer = 'short answer';
  const segments = buildSegments(answer, [
    { id: 'bad', span_start: 5, span_end: 900, status: 'supported' },
  ]);
  assert.equal(segments.length, 1);
  assert.equal(segments[0].claim, null);
  assert.equal(segments[0].text, answer);
});

test('the earliest span wins an overlap', () => {
  const answer = 'abcdefghij';
  const segments = buildSegments(answer, [
    { id: 'late', span_start: 3, span_end: 8, status: 'supported' },
    { id: 'early', span_start: 0, span_end: 5, status: 'contradicted' },
  ]);
  assert.equal(segments.map((segment) => segment.text).join(''), answer);
  assert.deepEqual(
    segments.filter((segment) => segment.claim).map((segment) => segment.claim.id),
    ['early'],
  );
});

test('plain text between spans is kept', () => {
  const segments = buildSegments('one two three', [
    { id: 'c', span_start: 4, span_end: 7, status: 'supported' },
  ]);
  assert.deepEqual(
    segments.map((segment) => [segment.text, segment.claim?.id || null]),
    [
      ['one ', null],
      ['two', 'c'],
      [' three', null],
    ],
  );
});

test('a run is live only while it may still be written to', () => {
  assert.equal(isLive('queued'), true);
  assert.equal(isLive('running'), true);
  assert.equal(isLive('completed'), false);
  assert.equal(isLive('partial'), false);
  assert.equal(isLive('failed'), false);
});

test('known events are narrated and unknown ones fall back to their type', () => {
  const settled = describeEvent({
    event_type: 'claim.settled',
    payload: { status: 'changed', reason_code: 'value_changed_after_generation' },
  });
  assert.equal(settled.label, 'Verdict recorded');
  assert.equal(settled.detail, 'value changed after generation');
  assert.equal(settled.tone, 'ok');

  const unknown = describeEvent({ event_type: 'something.new', payload: {} });
  assert.equal(unknown.label, 'something.new');
  assert.equal(unknown.tone, 'muted');

  const failed = describeEvent({ event_type: 'model.failed', payload: { stage: 'extract_claims' } });
  assert.equal(isProblem({ event_type: 'model.failed', payload: {} }), true);
  assert.equal(isProblem({ event_type: 'claim.settled', payload: {} }), false);
  assert.equal(failed.tone, 'danger');
});

test('a missing reason code reads as a sentence, not as an empty string', () => {
  assert.equal(reasonLabel(null), 'no reason code');
  // Scope is currency, period or per-unit basis — not only a replacement value.
  assert.equal(reasonLabel('scope_mismatch'),
    'values differ in scope (currency, period, per unit)');
  assert.equal(reasonLabel('brand_new_code'), 'brand new code');
});

test('urls and dates are shortened without inventing a date', () => {
  assert.equal(shortUrl('https://www.acme.test/pricing/'), 'www.acme.test/pricing');
  assert.equal(shortDate('2026-11-09T12:34:56Z'), '2026-11-09');
  assert.equal(shortDate(null), '—');
  assert.equal(shortDate('not a date'), 'not a date');
});

test('a cost with no recorded value is unknown, not free', () => {
  assert.equal(formatCost(null), '—');
  assert.equal(formatCost(undefined), '—');
  assert.equal(formatCost(''), '—');
  assert.equal(formatCost('not a number'), '—');
  assert.equal(formatCost(0), '$0');
  assert.equal(formatCost(0.00042), '$0.0004');
  assert.equal(formatCost(1.5), '$1.50');
});

test('a gateway route names the model that answered, not the one requested', () => {
  // A fallback answered: the request was for a Groq model, the response came
  // from local Ollama. The panel must not print the request as the answer.
  const fallback = {
    provider: 'ollama',
    model: 'groq/openai/gpt-oss-20b',
    resolved_model: 'llama3.2:1b',
  };
  const label = gatewayRoute(fallback);
  assert.ok(label.startsWith('ollama'), label);
  assert.ok(label.includes('llama3.2:1b'), label);
  assert.ok(!label.includes('groq/openai/gpt-oss-20b'), label);

  // No routing happened, so the resolved name is absent rather than repeated.
  assert.equal(gatewayRoute({ provider: 'groq', model: 'openai/gpt-oss-20b' }),
    gatewayRoute({ provider: 'groq', model: 'openai/gpt-oss-20b',
                   resolved_model: 'openai/gpt-oss-20b' }));

  // A row that recorded nothing is unrecorded, not blank.
  assert.equal(gatewayRoute({}), 'unrecorded route');
  assert.equal(gatewayRoute(null), 'unrecorded route');
});
