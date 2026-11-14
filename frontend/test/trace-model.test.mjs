/**
 * The trace panel's decisions, without a browser.
 *
 * Each case here is something a reader would only notice by trusting it: a
 * filter that hides the parent of the row it kept, a fallback shown as the
 * model that was asked for, an unpriced stage printed as free.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  SPAN_FILTERS,
  costCell,
  countSpans,
  flattenTrace,
  formatDuration,
  formatOffset,
  formatTokens,
  isModelSpan,
  isProblemSpan,
  slowestSpan,
  spanCost,
  spanDetail,
  spanLabel,
  spanTone,
  traceWindow,
  waterfallRows,
} from '../src/verify/traceModel.js';

function span(overrides) {
  return {
    id: overrides.id,
    ordinal: overrides.ordinal,
    parent_span_id: overrides.parent_span_id ?? null,
    stage: overrides.stage || 'research',
    operation: overrides.operation || 'search',
    attempt: overrides.attempt || 1,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    cost_source: 'none',
    latency_ms: 0,
    prompt_version: '',
    requested_model: '',
    resolved_model: '',
    tool_name: '',
    tool_arguments: {},
    cache_status: '',
    error_type: '',
    children: [],
    ...overrides,
  };
}

function tree() {
  const judge = span({
    id: 'judge',
    ordinal: 5,
    parent_span_id: 'claim',
    operation: 'judge_evidence',
    prompt_version: 'propose.v1',
    requested_model: 'mistral/mistral-small-latest',
    resolved_model: 'groq/llama-3.3-70b',
    cost_usd: 0.0001,
    cost_source: 'catalogue',
  });
  const search = span({
    id: 'search',
    ordinal: 4,
    parent_span_id: 'claim',
    tool_name: 'tavily_search',
    tool_arguments: { query: 'acme pro price' },
    cache_status: 'miss',
  });
  const claim = span({
    id: 'claim',
    ordinal: 3,
    parent_span_id: 'root',
    stage: 'claim',
    operation: 'verify_claim',
    children: [search, judge],
  });
  const extract = span({
    id: 'extract',
    ordinal: 1,
    parent_span_id: 'root',
    stage: 'extract',
    operation: 'model_call',
    prompt_version: 'extract.v1',
    requested_model: 'mistral/mistral-small-latest',
    resolved_model: 'mistral/mistral-small-latest',
    input_tokens: 1200,
    output_tokens: 80,
    cost_usd: 0.0002,
    cost_source: 'gateway',
  });
  const audit = span({
    id: 'audit',
    ordinal: 2,
    parent_span_id: 'root',
    stage: 'coverage',
    operation: 'audit',
    decision_result: 'checked 2/3',
  });
  return span({
    id: 'root',
    ordinal: 0,
    stage: 'run',
    operation: 'run',
    latency_ms: 4200,
    children: [extract, audit, claim],
  });
}

test('every filter the panel offers is exercised below', () => {
  assert.deepEqual(
    SPAN_FILTERS.map((entry) => entry.id),
    ['all', 'model', 'problems'],
  );
});

test('the flat view is depth-first, parents before children, in ordinal order', () => {
  const rows = flattenTrace(tree());
  assert.deepEqual(
    rows.map((row) => row.id),
    ['root', 'extract', 'audit', 'claim', 'search', 'judge'],
  );
  assert.deepEqual(
    rows.map((row) => row.depth),
    [0, 1, 1, 1, 2, 2],
  );
  assert.equal(rows.every((row) => row.context === false), true);
});

test('a model filter keeps the ancestors of what it matched, marked as context', () => {
  const rows = flattenTrace(tree(), 'model');
  assert.deepEqual(
    rows.map((row) => [row.id, row.context]),
    [
      ['root', true],
      ['extract', false],
      ['claim', true],
      ['judge', false],
    ],
  );
  // The audit span is not an ancestor of a model call, so it is gone.
  assert.equal(rows.some((row) => row.id === 'audit'), false);
});

test('a problems filter matches a failed span and a cache miss, not a quiet one', () => {
  const failed = span({
    id: 'failed',
    ordinal: 6,
    parent_span_id: 'root',
    stage: 'extract',
    operation: 'model_call',
    error_type: 'fatal',
  });
  const base = tree();
  base.children.push(failed);

  assert.equal(isProblemSpan(failed), true);
  assert.equal(isProblemSpan(span({ id: 'x', cache_status: 'miss' })), true);
  assert.equal(isProblemSpan(span({ id: 'y', cache_status: 'cached' })), false);

  const rows = flattenTrace(base, 'problems');
  assert.deepEqual(
    rows.filter((row) => !row.context).map((row) => row.id),
    ['search', 'failed'],
  );
  // Both problems live under the root; the root is the branch, not a match.
  assert.equal(rows[0].id, 'root');
  assert.equal(rows[0].context, true);
});

test('a filter with no match renders nothing rather than an empty shell', () => {
  const quiet = span({ id: 'only', ordinal: 0, stage: 'run', operation: 'run' });
  assert.deepEqual(flattenTrace(quiet, 'problems'), []);
  assert.deepEqual(flattenTrace(null), []);
});

test('a model span is one that names a prompt, not one with a familiar name', () => {
  assert.equal(isModelSpan(span({ id: 'a', prompt_version: 'extract.v2' })), true);
  assert.equal(isModelSpan(span({ id: 'b', operation: 'judge_evidence' })), false);
  assert.equal(
    isModelSpan(span({ id: 'c', operation: 'a_stage_nobody_has_written_yet', prompt_version: 'x.v1' })),
    true,
  );
});

test('counting reports how much of the tree a view holds', () => {
  assert.deepEqual(countSpans(tree(), 'all'), { total: 6, matching: 6 });
  assert.deepEqual(countSpans(tree(), 'model'), { total: 6, matching: 2 });
  assert.deepEqual(countSpans(tree(), 'problems'), { total: 6, matching: 1 });
  assert.deepEqual(countSpans(null), { total: 0, matching: 0 });
});

test('a fallback leads the detail line, because it is the surprising fact', () => {
  const detail = spanDetail(tree().children[2].children[1]);
  assert.match(detail, /^mistral\/mistral-small-latest → groq\/llama-3\.3-70b/);
  assert.match(detail, /propose\.v1/);
  assert.doesNotMatch(detail, /attempt 1/);
});

test('a tool span names its tool and what it was pointed at', () => {
  const detail = spanDetail(tree().children[2].children[0]);
  assert.match(detail, /tavily_search · acme pro price/);
  assert.match(detail, /miss/);
});

test('an error is in the detail, and a retry is only mentioned when it happened', () => {
  assert.match(spanDetail(span({ id: 'e', error_type: 'transient' })), /error: transient/);
  assert.match(spanDetail(span({ id: 'r', attempt: 2 })), /attempt 2/);
});

test('tone is a problem first, then a replay, then quiet', () => {
  assert.equal(spanTone(span({ id: 'a', error_type: 'fatal' })), 'danger');
  assert.equal(spanTone(span({ id: 'b', cache_status: 'snapshot' })), 'warn');
  assert.equal(spanTone(span({ id: 'c', cache_status: 'cached' })), 'ok');
  assert.equal(spanTone(span({ id: 'd' }), { context: true }), 'muted');
  assert.equal(spanTone(span({ id: 'e' })), 'muted');
});

test('an unpriced stage is not a free stage', () => {
  const unpriced = costCell({ gateway_usd: 0, estimated_usd: 0, unpriced_spans: 3 });
  assert.equal(unpriced.billedSpans, 0);
  assert.equal(unpriced.unpriced, 3);

  const billed = costCell({
    gateway_usd: 0.0002,
    estimated_usd: 0,
    gateway_spans: 1,
    unpriced_spans: 0,
  });
  assert.equal(billed.billedSpans, 1);
  assert.equal(billed.billed, 0.0002);

  // A gateway that reported zero still priced its calls: one span said
  // `cost_source: gateway`, so the zero is a recorded fact, not missing data.
  const zero = costCell({ gateway_usd: 0, estimated_usd: 0, gateway_spans: 2 });
  assert.equal(zero.billedSpans, 2);
  assert.equal(zero.billed, 0);
  assert.equal(zero.unpriced, 0);
});

test('a span priced at zero is priced, and a span with no source is not', () => {
  assert.deepEqual(spanCost({ cost_source: 'gateway', cost_usd: 0 }), {
    priced: true,
    value: 0,
  });
  assert.deepEqual(spanCost({ cost_source: 'catalogue', cost_usd: 0.0001 }), {
    priced: true,
    value: 0.0001,
  });
  assert.deepEqual(spanCost({ cost_source: 'none', cost_usd: 0 }), {
    priced: false,
    value: 0,
  });
  assert.deepEqual(spanCost({}), { priced: false, value: 0 });
});

test('durations read as durations, and a missing measurement is not zero', () => {
  assert.equal(formatDuration(0), '—');
  assert.equal(formatDuration(null), '—');
  assert.equal(formatDuration(250), '250 ms');
  assert.equal(formatDuration(1250), '1.3 s');
  assert.equal(formatDuration(42000), '42 s');
  assert.equal(formatDuration(65000), '1m 5s');
});

test('token counts stay readable in a table cell', () => {
  assert.equal(formatTokens(0), '0');
  assert.equal(formatTokens(999), '999');
  assert.equal(formatTokens(37669), '38k');
  assert.equal(formatTokens(1200000), '1.2M');
});

test('an operation nobody wrote copy for still gets a readable label', () => {
  assert.equal(spanLabel(span({ id: 'a', operation: 'judge_evidence' })), 'Judge evidence');
  assert.equal(spanLabel(span({ id: 'b', operation: 'settle_replacement' })), 'settle replacement');
  assert.equal(spanLabel({}), 'operation');
});

/* --- the waterfall -------------------------------------------------------
 * Bar geometry is the kind of arithmetic that fails quietly: an axis measured
 * over the wrong rows, a span clamped to the wrong edge, a zero-duration span
 * widened until it looks like work. None of it throws, so it is asserted here.
 */

function timed(id, stage, startedAt, endedAt, extra = {}) {
  return span({ id, stage, started_at: startedAt, ended_at: endedAt, ...extra });
}

test('the run window spans the earliest start to the latest end', () => {
  const win = traceWindow([
    timed('a', 'extract', '2026-03-01T10:00:02Z', '2026-03-01T10:00:04Z'),
    timed('b', 'research', '2026-03-01T10:00:00Z', '2026-03-01T10:00:01Z'),
    timed('c', 'claim', '2026-03-01T10:00:03Z', '2026-03-01T10:00:08Z'),
  ]);
  assert.equal(win.start, Date.parse('2026-03-01T10:00:00Z'));
  assert.equal(win.end, Date.parse('2026-03-01T10:00:08Z'));
  assert.equal(win.totalMs, 8000);
});

test('a span with no end falls back to its latency, not to zero', () => {
  const win = traceWindow([
    { started_at: '2026-03-01T10:00:00Z', latency_ms: 2500 },
  ]);
  assert.equal(win.totalMs, 2500);
});

test('no usable timestamps means no window, so no bars are invented', () => {
  assert.equal(traceWindow([]), null);
  assert.equal(traceWindow([span({ id: 'x' })]), null);
});

test('offsets and widths are proportions of the run, not of the filtered set', () => {
  const rows = [
    timed('a', 'extract', '2026-03-01T10:00:00Z', '2026-03-01T10:00:02Z'),
    timed('b', 'research', '2026-03-01T10:00:06Z', '2026-03-01T10:00:08Z'),
  ];
  const win = traceWindow(rows);
  const bars = waterfallRows([rows[1]], win);
  assert.equal(bars[0].startPct, 75, 'the second span starts three quarters in');
  assert.equal(bars[0].widthPct, 25);
  assert.equal(bars[0].offsetMs, 6000);
});

test('a bar never runs past the right edge of the axis', () => {
  const win = traceWindow([
    timed('a', 'extract', '2026-03-01T10:00:00Z', '2026-03-01T10:00:01Z'),
  ]);
  const [row] = waterfallRows(
    [timed('late', 'extract', '2026-03-01T10:00:00Z', '2026-03-01T10:00:09Z')],
    win,
  );
  assert.equal(row.startPct, 0);
  assert.equal(row.widthPct, 100);
});

test('a zero-length span keeps zero width but is still placed', () => {
  const win = traceWindow([
    timed('a', 'extract', '2026-03-01T10:00:00Z', '2026-03-01T10:00:04Z'),
  ]);
  const [row] = waterfallRows(
    [timed('instant', 'settle', '2026-03-01T10:00:02Z', '2026-03-01T10:00:02Z')],
    win,
  );
  assert.equal(row.startPct, 50);
  assert.equal(row.widthPct, 0);
});

test('without a window the rows carry no geometry at all', () => {
  const [row] = waterfallRows([timed('a', 'extract', '2026-03-01T10:00:00Z', '2026-03-01T10:00:04Z')], null);
  assert.equal(row.startPct, null);
  assert.equal(row.widthPct, null);
  assert.equal(row.durationMs, 4000, 'the duration is still known');
});

test('the slowest step is a step, never the run itself', () => {
  const rows = [
    timed('root', 'run', '2026-03-01T10:00:00Z', '2026-03-01T10:00:20Z', { operation: 'run' }),
    timed('search', 'research', '2026-03-01T10:00:00Z', '2026-03-01T10:00:12Z'),
    timed('extract', 'extract', '2026-03-01T10:00:12Z', '2026-03-01T10:00:14Z'),
  ];
  const win = traceWindow(rows);
  const slowest = slowestSpan(rows, win);
  assert.equal(slowest.id, 'search');
  assert.equal(slowest.durationMs, 12000);
  assert.equal(slowest.share, 0.6);
});

test('a context row is never named the slowest step', () => {
  const rows = [
    timed('parent', 'claim', '2026-03-01T10:00:00Z', '2026-03-01T10:00:09Z', { context: true }),
    timed('child', 'research', '2026-03-01T10:00:01Z', '2026-03-01T10:00:02Z'),
  ];
  const slowest = slowestSpan(rows, traceWindow(rows));
  assert.equal(slowest.id, 'child');
});

test('offsets read as a clock, and a negative offset does not read as the past', () => {
  assert.equal(formatOffset(0), '+0');
  assert.equal(formatOffset(-40), '+0');
  assert.equal(formatOffset(1500), '+1.5 s');
  assert.equal(formatOffset(null), '+0');
});

