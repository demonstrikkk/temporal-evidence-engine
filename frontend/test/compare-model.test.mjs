/**
 * The compare matrix's presentation rules.
 *
 * The failure this file exists to prevent is a confident zero. A registry model
 * has no calls, and an unpriced call has no cost — printing `0` or `$0.00` for
 * either would claim the model was used and was free. The helpers must hand the
 * page a null, the page must render an em dash, and sorting must never let an
 * unmeasured row win a metric it does not have.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  evidenceLabel,
  filterRows,
  formatLatency,
  formatTokens,
  formatUsd,
  isMeasured,
  isPasted,
  isRegistry,
  observedTone,
  orderLabel,
  rateLabel,
  sortRows,
  verdictMix,
  vendorLabel,
} from '../src/verify/compareModel.js';

const measured = {
  model: 'mistral-small-latest',
  origin: 'measured',
  providers: ['mistral'],
  calls: 4,
  input_tokens: 1200,
  output_tokens: 640,
  avg_cost_usd: 0.0002,
  avg_latency_ms: 900,
  timed_calls: 4,
  error_calls: 1,
  error_rate: 0.25,
  answers: 2,
  claims: 6,
  settled: 5,
  supported: 3,
  changed: 1,
  contradicted: 1,
  unverifiable: 0,
  pending: 1,
  supported_rate: 0.6,
  changed_rate: 0.2,
  contradicted_rate: 0.2,
  unverifiable_rate: 0,
  due: 2,
};

const registry = {
  model: 'big-pickle',
  origin: 'registry',
  vendor: 'stealth',
  providers: [],
  calls: null,
  avg_cost_usd: null,
  avg_latency_ms: null,
  answers: 0,
  claims: 0,
  settled: 0,
  due: 0,
};

const pasted = {
  model: '',
  origin: 'pasted',
  vendor: '',
  providers: [],
  calls: null,
  avg_cost_usd: null,
  avg_latency_ms: null,
  answers: 17,
  claims: 47,
  settled: 42,
  supported: 23,
  changed: 0,
  contradicted: 9,
  unverifiable: 10,
  pending: 5,
  due: 18,
  supported_rate: 0.5476,
};

test('a missing measurement is an em dash, never a zero', () => {
  assert.equal(rateLabel(null), '—');
  assert.equal(formatUsd(null), '—');
  assert.equal(formatTokens(null), '—');
  assert.equal(formatLatency(null), '—');
  assert.equal(rateLabel(0), '0%');
  assert.equal(formatUsd(0), '$0.00');
});

test('a sub-cent call keeps its digits', () => {
  assert.equal(formatUsd(0.0002), '$0.000200');
  assert.equal(formatUsd(0.05), '$0.0500');
  assert.equal(formatUsd(12.5), '$12.50');
  assert.equal(formatUsd(0.000001), '$0.000001');
});

test('tokens and latency shorten without lying about the order of magnitude', () => {
  assert.equal(formatTokens(999), '999');
  assert.equal(formatTokens(1200), '1.2k');
  assert.equal(formatTokens(24000), '24k');
  assert.equal(formatTokens(1_500_000), '1.5M');
  assert.equal(formatLatency(830), '830 ms');
  assert.equal(formatLatency(1500), '1.5 s');
  assert.equal(formatLatency(12000), '12 s');
});

test('the verdict mix carries settled counts and leaves pending out', () => {
  const mix = verdictMix(measured);
  assert.deepEqual(mix.map((segment) => segment.id), [
    'supported',
    'changed',
    'contradicted',
    'unverifiable',
  ]);
  assert.deepEqual(mix.map((segment) => segment.count), [3, 1, 1, 0]);
  assert.deepEqual(mix.map((segment) => segment.share), [0.6, 0.2, 0.2, 0]);
  // Pending belongs to no segment: the bars must add up to the settled claims.
  assert.equal(mix.reduce((total, segment) => total + segment.count, 0), measured.settled);
});

test('the mix falls back to the settled denominator when the API sent no rate', () => {
  const mix = verdictMix({ settled: 4, supported: 1, changed: 1, contradicted: 2 });
  assert.deepEqual(mix.map((segment) => segment.share), [0.25, 0.25, 0.5, 0]);
});

test('unmeasured rows stay below measured ones in both directions', () => {
  const rows = [registry, measured];
  for (const dir of ['asc', 'desc']) {
    for (const key of ['calls', 'cost', 'latency', 'supported', 'due', 'evidence']) {
      const sorted = sortRows(rows, key, dir);
      assert.equal(sorted.at(-1).origin, 'registry', `${key} ${dir} lifted the registry row`);
    }
  }
});

test('a measured row with no value for the key still outranks the registry', () => {
  const noCalls = { ...measured, model: 'scanned-only', calls: null, avg_cost_usd: null };
  const sorted = sortRows([registry, noCalls], 'calls', 'asc');
  assert.deepEqual(sorted.map((row) => row.model), ['scanned-only', 'big-pickle']);
});

test('sorting by calls puts the busiest model first when descending', () => {
  const quiet = { ...measured, model: 'quiet', calls: 1, claims: 0 };
  const busy = { ...measured, model: 'busy', calls: 9, claims: 0 };
  assert.deepEqual(
    sortRows([quiet, busy], 'calls', 'desc').map((row) => row.model),
    ['busy', 'quiet'],
  );
  assert.deepEqual(
    sortRows([quiet, busy], 'calls', 'asc').map((row) => row.model),
    ['quiet', 'busy'],
  );
});

test('a tie falls back to the model id so the table is stable', () => {
  const a = { ...measured, model: 'alpha' };
  const b = { ...measured, model: 'beta' };
  assert.deepEqual(
    sortRows([b, a], 'calls', 'desc').map((row) => row.model),
    ['alpha', 'beta'],
  );
});

test('the measured filter hides catalogue rows without deleting them', () => {
  const rows = [measured, pasted, registry];
  assert.deepEqual(filterRows(rows, 'measured'), [measured, pasted]);
  assert.deepEqual(filterRows(rows, 'all'), rows);
});

test('a pasted group is evidence, but never ranked as a model', () => {
  for (const dir of ['asc', 'desc']) {
    for (const key of ['calls', 'cost', 'latency', 'supported', 'due', 'evidence', 'model']) {
      const sorted = sortRows([registry, pasted, measured], key, dir);
      assert.deepEqual(
        sorted.map((row) => row.origin),
        ['measured', 'pasted', 'registry'],
        `${key} ${dir} reordered the bands`,
      );
    }
  }
});

test('a pasted group says what it is instead of naming a model', () => {
  assert.equal(evidenceLabel(pasted), 'pasted answers — model not recorded');
  assert.equal(vendorLabel(pasted), 'not recorded');
  assert.equal(isPasted(pasted), true);
  assert.equal(isMeasured(pasted), false);
  assert.equal(isRegistry(pasted), false);
});

test('the registry keeps its observed state and the vendor as recorded', () => {
  assert.equal(observedTone('ok 2026-09-27'), 'ok');
  assert.equal(observedTone('intermittent 2026-09-27'), 'partial');
  assert.equal(observedTone(''), 'none');
  assert.equal(vendorLabel(registry), 'stealth');
  assert.equal(vendorLabel({ ...registry, vendor: '' }), 'not recorded');
  assert.equal(vendorLabel(measured), 'mistral');
});

test('a row says what evidence it has instead of claiming none', () => {
  assert.equal(evidenceLabel(registry), 'catalogue entry — no calls recorded');
  assert.equal(evidenceLabel({ ...measured, answers: 0 }), 'no scanned answers yet');
  assert.equal(evidenceLabel(measured), '2 scanned answers');
});

test('the table says which order it is in, including the default with no column', () => {
  assert.equal(orderLabel({ key: 'evidence', dir: 'desc' }), 'Ordered by evidence, most measured first');
  assert.equal(orderLabel({ key: 'calls', dir: 'desc' }), 'Ordered by recorded calls, descending');
  assert.equal(orderLabel({ key: 'calls', dir: 'asc' }), 'Ordered by recorded calls, ascending');
  assert.equal(orderLabel({ key: 'cost', dir: 'asc' }), 'Ordered by average cost per call, ascending');
  // A key the table does not know falls back to the order it actually uses,
  // rather than announcing a sort that is not applied.
  assert.equal(orderLabel({ key: 'nonsense', dir: 'asc' }), 'Ordered by evidence, most measured first');
  assert.equal(orderLabel(undefined), 'Ordered by evidence, most measured first');
});
