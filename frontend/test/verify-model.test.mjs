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
  ago,
  buildSegments,
  claimCounts,
  describeEvent,
  formatCost,
  freshnessTone,
  freshnessWord,
  gatewayRoute,
  halfLifeLabel,
  isLive,
  isProblem,
  reasonLabel,
  researchSummary,
  runCounts,
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

test('a recommended recheck in the future reads as future, not as overdue', () => {
  const future = new Date(Date.now() + 3 * 86_400_000).toISOString();
  const past = new Date(Date.now() - 2 * 86_400_000).toISOString();
  assert.match(ago(future), /^in \d+d$/);
  assert.match(ago(past), /^\d+d ago$/);
  assert.equal(ago(null), '');
  assert.equal(ago('not a date'), '');
});

test('freshness bands are words, and a missing score is not "stale"', () => {
  // The backend owns the number; these bands only pick a word. The distinction
  // that matters is "never verified" (no score) versus "verified long ago" (0).
  assert.equal(freshnessWord(100), 'current');
  assert.equal(freshnessWord(70), 'current');
  assert.equal(freshnessWord(69), 'ageing');
  assert.equal(freshnessWord(40), 'ageing');
  assert.equal(freshnessWord(39), 'stale');
  assert.equal(freshnessWord(0), 'stale');
  assert.equal(freshnessWord(null), 'not verified');
  assert.equal(freshnessWord(undefined), 'not verified');
  assert.equal(freshnessWord('nonsense'), 'not verified');
  assert.equal(freshnessTone(null), 'muted');
  assert.equal(freshnessTone(0), 'danger');
});

test('a half-life reads as a span a human can check', () => {
  assert.equal(halfLifeLabel(0.25), '15 min');
  assert.equal(halfLifeLabel(6), '6 h');
  assert.equal(halfLifeLabel(24), '1 d');
  assert.equal(halfLifeLabel(720), '30 d');
  assert.equal(halfLifeLabel(null), '—');
  assert.equal(halfLifeLabel(0), '—');
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

test('a research ledger counts events, and never invents a fetch', () => {
  const empty = researchSummary([]);
  assert.equal(empty.plans, 0);
  assert.equal(empty.stored, 0);
  assert.equal(empty.failed, 0);
  assert.deepEqual(empty.sources, []);

  // One page quoted by three claims is one stored source, not three.
  const events = [
    { event_type: 'research.planned', payload: { queries: ['chatgpt plus price', 'openai leadership'] } },
    { event_type: 'source.stored', created_at: '2026-11-12T14:00:00Z',
      payload: { url: 'https://openai.com/pricing', revision_id: 'rev_1', chars: 812, published_date: '2026-11-01' } },
    { event_type: 'source.stored', created_at: '2026-11-12T14:00:02Z',
      payload: { url: 'https://openai.com/pricing', revision_id: 'rev_1', chars: 812 } },
    { event_type: 'source.failed', payload: { url: 'https://example.com/x', stage: 'extract', error: 'no text returned' } },
    { event_type: 'claim.evidence_reused', payload: { claim_id: 'clm_1' } },
    { event_type: 'model.call', payload: {} },
  ];
  const summary = researchSummary(events);
  assert.equal(summary.plans, 1);
  assert.equal(summary.planned, 2);
  assert.equal(summary.stored, 1);
  assert.equal(summary.failed, 1);
  assert.equal(summary.reused, 1);
  assert.equal(summary.sources.length, 1);
  assert.equal(summary.sources[0].chars, 812);
  assert.equal(summary.sources[0].publishedAt, '2026-11-01');
});

test('a refused query plan is reported, and a plan with no queries still counts', () => {
  const summary = researchSummary([
    { event_type: 'research.planned', payload: { queries: [] } },
    { event_type: 'research.budget_exhausted', payload: { queries_dropped: 3, budget_mode: 'offline' } },
  ]);
  // Offline mode asks nothing. "Planned zero queries" is a statement about the
  // run, not the same as no research stage at all.
  assert.equal(summary.plans, 1);
  assert.equal(summary.planned, 0);
  assert.equal(summary.budgetStops, 1);
  assert.equal(summary.stored, 0);
});

test('a payload that is not an object cannot fake a source', () => {
  const summary = researchSummary([
    { event_type: 'source.stored', payload: 'not a dict' },
    { event_type: 'source.stored', payload: { url: 'https://a.example/p', revision_id: 'rev_9' } },
    { event_type: 'research.planned' },
    null,
  ]);
  assert.equal(summary.stored, 1);
  assert.equal(summary.plans, 1);
  assert.equal(summary.sources[0].chars, 0);
  assert.equal(summary.sources[0].publishedAt, null);
});

test('a run with no abstention still reports zero abstentions, not undefined', () => {
  // The store only sends the statuses that occurred. A completed run with no
  // abstention therefore has no `unverifiable` key, and reading it straight
  // from the payload printed "undefined abstained" and "no claims" on a real
  // run. Every verdict key must exist.
  const claims = [
    { status: 'supported' }, { status: 'supported' }, { status: 'contradicted' },
  ];
  const counts = runCounts(claims, { supported: 2, contradicted: 1, claims: 3 });
  assert.equal(counts.unverifiable, 0);
  assert.equal(counts.pending, 0);
  assert.equal(counts.changed, 0);
  assert.equal(counts.total, 3);
  assert.equal(counts.supported, 2);

  // A recorded count still wins over the derived one.
  const recorded = runCounts(claims, { unverifiable: 4, total: 9 });
  assert.equal(recorded.unverifiable, 4);
  assert.equal(recorded.total, 9);

  // No recorded counters at all: derive everything.
  const derived = runCounts(claims, null);
  assert.equal(derived.contradicted, 1);
  assert.equal(derived.total, 3);
});
