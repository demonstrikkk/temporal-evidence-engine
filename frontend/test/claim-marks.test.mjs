/**
 * Claim marks on a rendered answer.
 *
 * The extractor's offsets index the raw answer. Markdown rendering deletes
 * syntax characters, so the only sound way to keep the marks is to intersect
 * each stored range with the *source position* remark records on every text
 * node — never to search the rendered text for the claim's words, which would
 * put a highlight on the wrong sentence the first time two sentences repeat.
 *
 * The trees below are hand-built mdast, exactly the shape remark produces for
 * the prose in each comment, so the test does not depend on the parser and can
 * state the offsets it is asserting about.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { markClaims } from '../src/components/claimMarks.js';

/** mdast text node with the source offsets remark would record. */
function text(value, start, end) {
  return {
    type: 'text',
    value,
    position: { start: { offset: start }, end: { offset: end } },
  };
}

function root(...children) {
  return { type: 'root', children };
}

function paragraph(...children) {
  return { type: 'paragraph', children };
}

/** Flatten every text value in the tree, in document order. */
function rendered(node) {
  if (node.type === 'text') return node.value;
  return (node.children || []).map(rendered).join('');
}

/** Every claim mark in the tree, in document order. */
function marks(node, out = []) {
  if (node.type === 'claimSpan') {
    out.push({
      id: node.data.hProperties.dataClaim,
      status: (node.data.hProperties.className || []).find((c) => c.startsWith('is-')),
      text: rendered(node),
      tag: node.data.hName,
    });
    return out;
  }
  for (const child of node.children || []) marks(child, out);
  return out;
}

test('a claim inside one text node splits it and loses no characters', () => {
  const answer = 'Hello world.';
  const tree = root(paragraph(text(answer, 0, answer.length)));

  const stats = markClaims(tree, {
    answer,
    claims: [{ id: 'c1', claim_text: 'world', span_start: 6, span_end: 11 }],
  });

  assert.deepEqual(stats, { marked: 1, unplaced: 0, unreadable: 0 });
  assert.equal(rendered(tree), answer);
  assert.deepEqual(marks(tree), [{ id: 'c1', status: 'is-pending', text: 'world', tag: 'mark' }]);

  const para = tree.children[0];
  assert.equal(para.children.length, 3);
  assert.equal(rendered(para.children[0]), 'Hello ');
  assert.equal(rendered(para.children[2]), '.');
});

test('a claim that crosses bold marks the words on both sides, not the asterisks', () => {
  // "Cost is **$20** now."
  const answer = 'Cost is **$20** now.';
  const tree = root(
    paragraph(
      text('Cost is ', 0, 8),
      {
        type: 'strong',
        children: [text('$20', 10, 13)],
        position: { start: { offset: 8 }, end: { offset: 15 } },
      },
      text(' now.', 15, 20),
    ),
  );

  markClaims(tree, {
    answer,
    claims: [
      {
        id: 'c1',
        claim_text: 'Cost is **$20**',
        span_start: 0,
        span_end: 15,
        status: 'supported',
      },
    ],
  });

  const para = tree.children[0];
  assert.equal(rendered(para), 'Cost is $20 now.');
  const found = marks(tree);
  assert.deepEqual(found.map((m) => m.text), ['Cost is ', '$20']);
  assert.ok(found.every((m) => m.status === 'is-supported'));
});

test('a text node whose source has escapes is left alone and counted', () => {
  // Raw "a \*b\*" parses to the text node value "a *b*": offsets cannot address it.
  const answer = 'a \\*b\\*';
  const tree = root(paragraph(text('a *b*', 0, answer.length)));

  const stats = markClaims(tree, {
    answer,
    claims: [{ id: 'c1', claim_text: 'a *b*', span_start: 0, span_end: 5 }],
  });

  assert.equal(stats.unreadable, 1);
  assert.equal(stats.marked, 0);
  assert.equal(stats.unplaced, 1);
  assert.equal(rendered(tree), 'a *b*');
  assert.deepEqual(marks(tree), []);
});

test('text inside a link is never wrapped in an interactive mark', () => {
  const answer = 'See the pricing page.';
  const tree = root(
    paragraph(
      text('See the ', 0, 8),
      {
        type: 'link',
        url: 'https://example.test',
        children: [text('pricing page', 8, 20)],
        position: { start: { offset: 8 }, end: { offset: 20 } },
      },
      text('.', 20, 21),
    ),
  );

  const stats = markClaims(tree, {
    answer,
    claims: [{ id: 'c1', claim_text: 'pricing page', span_start: 8, span_end: 20 }],
  });

  assert.equal(stats.marked, 0);
  assert.equal(stats.unplaced, 1);
  assert.equal(rendered(tree), 'See the pricing page.');
  assert.deepEqual(marks(tree), []);
});

test('overlapping claims resolve the way the marked view resolves them', () => {
  // Earliest start wins the overlap; the later claim keeps only its tail.
  const answer = 'abcdefghi';
  const tree = root(paragraph(text(answer, 0, 9)));

  const stats = markClaims(tree, {
    answer,
    claims: [
      { id: 'late', claim_text: 'defgh', span_start: 3, span_end: 8 },
      { id: 'early', claim_text: 'abcde', span_start: 0, span_end: 5 },
    ],
  });

  assert.equal(stats.marked, 2);
  assert.equal(rendered(tree), answer);
  assert.deepEqual(marks(tree), [
    { id: 'early', status: 'is-pending', text: 'abcde', tag: 'mark' },
    { id: 'late', status: 'is-pending', text: 'fgh', tag: 'mark' },
  ]);
});

test('a stale offset is dropped, never clamped into the text', () => {
  const answer = 'Short answer.';
  const tree = root(paragraph(text(answer, 0, answer.length)));

  const stats = markClaims(tree, {
    answer,
    claims: [{ id: 'c1', claim_text: 'nowhere', span_start: 40, span_end: 47 }],
  });

  assert.deepEqual(stats, { marked: 0, unplaced: 0, unreadable: 0 });
  assert.equal(rendered(tree), answer);
  assert.deepEqual(marks(tree), []);
});

test('a text node with no source position is left as it is', () => {
  const answer = 'Generated.';
  const tree = root(paragraph({ type: 'text', value: 'Generated.' }));

  const stats = markClaims(tree, {
    answer,
    claims: [{ id: 'c1', claim_text: 'Generated', span_start: 0, span_end: 9 }],
  });

  assert.equal(stats.marked, 0);
  assert.equal(rendered(tree), 'Generated.');
});
