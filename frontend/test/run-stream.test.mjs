/**
 * Regressions in the live run stream's client half.
 *
 * These all failed silently. The stream connected, the header showed progress,
 * and the feed said "Waiting for the first event…" for the whole run because
 * every frame was dropped before it reached state. No error was logged, so a
 * build and a type check both passed.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  eventKey,
  normaliseEvent,
  tally,
} from '../src/hooks/useRunStream.js';

test('an already-parsed frame is applied, not re-parsed', () => {
  // `readEventStream` JSON.parses each `data:` line, so this object is exactly
  // what `onFrame` receives. `apply` used to call JSON.parse on it, throw, and
  // swallow the throw -- so no event ever reached state.
  const frame = {
    type: 'source.fetched',
    stage: 'FETCHING',
    url: 'https://example.test/a',
    message: 'Fetched https://example.test/a',
    timestamp: '2026-11-09T06:19:10.000Z',
  };
  const ev = normaliseEvent(frame);
  assert.ok(ev, 'the frame was dropped');
  assert.equal(ev.type, 'source.fetched');
  assert.equal(ev.url, 'https://example.test/a');
});

test('a named frame keeps the event name as its type', () => {
  // The hook merges `{...data, type: event}`; the server also puts `type` in
  // the payload, and the frame name wins.
  const ev = normaliseEvent({
    event: 'record.extracted',
    ...{ type: 'record.extracted', data: { count: 3 } },
  });
  assert.equal(ev.type, 'record.extracted');
  assert.equal(ev.data.count, 3);
});

test('a JSON string is still accepted (EventSource shape)', () => {
  const ev = normaliseEvent('{"type":"run.completed","status":"COMPLETED"}');
  assert.equal(ev.type, 'run.completed');
});

test('garbage and typeless payloads are rejected, not half-applied', () => {
  assert.equal(normaliseEvent('not json'), null);
  assert.equal(normaliseEvent(null), null);
  assert.equal(normaliseEvent({ stage: 'FETCHING' }), null);
});

test('two pages that each extracted records stay two events', () => {
  // The old fingerprint keyed `record.*` on qualifier|value|reason, all empty
  // on extraction, so a ten-page run kept the first and dropped nine.
  const a = eventKey({
    type: 'record.extracted',
    timestamp: '2026-11-09T06:20:01.100Z',
    url: 'https://a.test',
    data: { count: 2 },
  });
  const b = eventKey({
    type: 'record.extracted',
    timestamp: '2026-11-09T06:20:02.200Z',
    url: 'https://b.test',
    data: { count: 5 },
  });
  assert.notEqual(a, b);
});

test('a replayed event keeps the same key so a reconnect does not duplicate it', () => {
  const ev = {
    type: 'source.fetched',
    timestamp: '2026-11-09T06:19:10.000Z',
    stage: 'FETCHING',
    url: 'https://example.test/a',
    message: 'Fetched https://example.test/a',
  };
  assert.equal(eventKey({ ...ev }), eventKey({ ...ev }));
});

test('fetch failures and record counts are tallied', () => {
  const counts = {
    sourcesFetched: 0, sourcesFailed: 0, sourcesReused: 0, extracted: 0,
    stages: 0, sourcesDiscovered: 0, verified: 0, needsReview: 0,
    rejected: 0, merged: 0,
  };
  tally(counts, { type: 'source.failed', url: 'https://x.test' });
  tally(counts, { type: 'record.extracted', data: { count: 4 } });
  tally(counts, { type: 'record.extracted', data: { count: 0 } });
  assert.equal(counts.sourcesFailed, 1);
  assert.equal(counts.extracted, 4, 'off-topic pages must not count as records');
});
