/**
 * Claim marks, drawn on the rendered answer.
 *
 * The extractor stores a byte range per claim, and that range indexes the raw
 * answer — the characters the model typed, `**` and `|` and `` ` `` included.
 * Rendering the answer as Markdown deletes those syntax characters, so a stored
 * offset no longer addresses the text a reader sees. That is why the marked
 * view existed as a separate, unformatted surface.
 *
 * This closes the gap without touching an offset. `remark` records a source
 * position on every text node, so each text node knows the exact raw slice it
 * came from — `June 2024` inside `**June 2024**` carries the offsets of the
 * words, not of the asterisks. A claim range is intersected with those slices,
 * and the intersecting part of the text node is wrapped in a `<mark>` that
 * `Markdown.jsx` renders as the same interactive claim button the marked view
 * uses. Formatting comes from the tree; marks come from the offsets; neither
 * one is guessed by searching for a string.
 *
 * Two honest limits, both handled rather than hidden:
 *
 * - A text node whose raw slice differs from its parsed value (a backslash
 *   escape, an HTML entity) cannot be addressed by offset here. It is left
 *   unmarked and counted in `unreadable` — the marked view still shows it.
 * - Text inside a link is not marked, because a button inside an anchor is
 *   invalid and a reader cannot click both.
 *
 * The function is pure and has no dependencies: it walks the tree it is given
 * and reports what it placed.
 */

/** Absolute offsets on a node, when the parser recorded them. */
function offsets(node) {
  const start = node?.position?.start?.offset;
  const end = node?.position?.end?.offset;
  if (!Number.isInteger(start) || !Number.isInteger(end) || end <= start) return null;
  return [start, end];
}

function textNode(value) {
  return { type: 'text', value };
}

function claimNode(claim, value) {
  const status = claim.status || 'pending';
  return {
    type: 'claimSpan',
    data: {
      // mdast-util-to-hast turns an unknown node into this element, with these
      // properties and the children below. `mark` is the one tag Markdown never
      // produces on its own, so overriding it cannot collide with a model's
      // prose.
      hName: 'mark',
      hProperties: {
        className: ['claim-mark', `is-${status}`],
        dataClaim: claim.id,
        title: claim.claim_text ? `${status}: ${claim.claim_text}` : status,
      },
    },
    children: [textNode(value)],
  };
}

function splitText(node, ctx) {
  const range = offsets(node);
  if (!range) return [node];
  const [start, end] = range;
  const raw = ctx.answer.slice(start, end);
  if (raw !== node.value) {
    // Escapes and entities make the parsed value shorter than its source; no
    // offset arithmetic can address it reliably.
    ctx.unreadable += 1;
    return [node];
  }

  const runs = [];
  let cursor = start;
  for (const claim of ctx.claims) {
    if (claim.span_end <= cursor || claim.span_start >= end) continue;
    // Earliest span wins an overlap, exactly as buildSegments decides it for
    // the marked view, so the two views never disagree about who owns a word.
    const from = Math.max(claim.span_start, cursor, start);
    const to = Math.min(claim.span_end, end);
    if (to <= from) continue;
    if (from > cursor) runs.push(textNode(ctx.answer.slice(cursor, from)));
    runs.push(claimNode(claim, ctx.answer.slice(from, to)));
    ctx.placed.add(claim.id);
    cursor = to;
  }
  if (cursor < end) runs.push(textNode(ctx.answer.slice(cursor, end)));
  return runs.length ? runs : [node];
}

/** Links own their content: never nest an interactive mark inside one. */
const OPAQUE = new Set(['link', 'linkReference']);

function walk(node, ctx) {
  if (!Array.isArray(node.children)) return;
  const out = [];
  for (const child of node.children) {
    if (child.type === 'text' && offsets(child)) {
      out.push(...splitText(child, ctx));
      continue;
    }
    if (!OPAQUE.has(child.type)) walk(child, ctx);
    out.push(child);
  }
  node.children = out;
}

/**
 * Mark every claim range that lands on text this tree renders.
 *
 * @param {object} tree   mdast root, mutated in place
 * @param {{answer: string, claims: Array<object>}} options
 * @returns {{marked: number, unplaced: number, unreadable: number}}
 *   `marked` counts claims with at least one mark, `unplaced` counts valid
 *   ranges that no text node could carry (link text, code), and `unreadable`
 *   counts text nodes whose source could not be addressed by offset.
 */
export function markClaims(tree, { answer, claims } = {}) {
  const text = String(answer ?? '');
  const list = (claims || [])
    .filter(
      (claim) =>
        Number.isInteger(claim?.span_start) &&
        Number.isInteger(claim?.span_end) &&
        claim.span_start >= 0 &&
        claim.span_end > claim.span_start &&
        claim.span_end <= text.length,
    )
    .sort((a, b) => a.span_start - b.span_start || b.span_end - a.span_end);

  if (!list.length || !tree) {
    return { marked: 0, unplaced: list.length, unreadable: 0 };
  }

  const ctx = { answer: text, claims: list, placed: new Set(), unreadable: 0 };
  walk(tree, ctx);

  return {
    marked: ctx.placed.size,
    unplaced: list.filter((claim) => !ctx.placed.has(claim.id)).length,
    unreadable: ctx.unreadable,
  };
}

export default markClaims;
