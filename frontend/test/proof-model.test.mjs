/**
 * The proof graph's presentation rules.
 *
 * The graph used to be drawn from hard-coded coordinates, which is why it was
 * clipped and why nothing here caught it. The layout is now a projection of the
 * rows, so what can go wrong silently is the projection: an unaccepted edge
 * coloured as accepted, an undated row promoted onto the event-date line, or a
 * summary that counts rows the graph never drew.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { buildProofNodes, proofSummary, relationshipTone } from '../src/verify/proofModel.js';

const claim = { claim_text: 'Acme Pro costs $99 per month', status: 'supported' };

function row(overrides = {}) {
  return {
    id: 'ev-1',
    quote_text: 'Acme Pro costs $99 per month',
    relationship_proposed: 'supports',
    settlement_status: 'accepted',
    event_date: '2026-03-01',
    event_date_status: 'explicit',
    published_date: '2026-04-01',
    source_url: 'https://acme.test/pricing',
    ...overrides,
  };
}

test('dated rows are ordered by event date, oldest first', () => {
  const model = buildProofNodes(claim, [
    row({ id: 'b', event_date: '2026-05-01' }),
    row({ id: 'a', event_date: '2026-01-01' }),
  ]);
  assert.deepEqual(model.nodes.map((node) => node.id), ['a', 'b']);
});

test('undated rows come last and never inherit the publication date', () => {
  const model = buildProofNodes(claim, [
    row({ id: 'undated', event_date: null, event_date_status: 'missing' }),
    row({ id: 'dated', event_date: '2026-02-01' }),
  ]);
  assert.deepEqual(model.nodes.map((node) => node.id), ['dated', 'undated']);
  const undated = model.nodes[1];
  assert.equal(undated.eventDate, '');
  assert.equal(undated.eventMissing, true);
  assert.equal(undated.publishedDate, '2026-04-01', 'the publication date is kept, not substituted');
});

test('a string that is not a date is undated, not sorted as one', () => {
  const model = buildProofNodes(claim, [row({ event_date: 'sometime in 2026' })]);
  assert.equal(model.nodes[0].eventMissing, true);
});

test('an unaccepted supports proposal is not drawn as a support', () => {
  const model = buildProofNodes(claim, [
    row({ settlement_status: 'rejected', relationship_proposed: 'supports' }),
  ]);
  assert.equal(model.nodes[0].tone, 'muted');
  assert.equal(model.nodes[0].accepted, false);
});

test('accepted relationships get their own tone', () => {
  const nodes = buildProofNodes(claim, [
    row({ id: 's', relationship_proposed: 'supports' }),
    row({ id: 'c', relationship_proposed: 'contradicts' }),
    row({ id: 'm', relationship_proposed: 'mentions_only' }),
  ]).nodes;
  assert.equal(nodes.find((node) => node.id === 's').tone, 'ok');
  assert.equal(nodes.find((node) => node.id === 'c').tone, 'danger');
  assert.equal(nodes.find((node) => node.id === 'm').tone, 'info');
});

test('the settled relationship wins over the proposed one', () => {
  const model = buildProofNodes(claim, [
    row({ relationship_settled: 'contradicts', relationship_proposed: 'supports' }),
  ]);
  assert.equal(model.nodes[0].relationship, 'contradicts');
  assert.equal(model.nodes[0].tone, 'danger');
});

test('tone is undefined-safe for a row with no relationship at all', () => {
  assert.equal(relationshipTone({ accepted: true, relationship: '' }), 'muted');
  assert.equal(relationshipTone(null), 'muted');
});

test('the summary counts what the graph actually draws', () => {
  const model = buildProofNodes(claim, [
    row({ id: 'a', event_date: '2026-01-01' }),
    row({ id: 'b', event_date: null }),
    row({ id: 'c', event_date: '2026-02-01', relationship_proposed: 'contradicts' }),
  ]);
  assert.equal(proofSummary(model), '3 rows · 3 settled · 2 dated · 1 undated · 1 contradicting');
});

test('an empty evidence set says so instead of printing zeros', () => {
  assert.equal(proofSummary(buildProofNodes(claim, [])), 'no evidence rows');
});
