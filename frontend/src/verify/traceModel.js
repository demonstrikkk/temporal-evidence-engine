/**
 * The execution trace, as presentation logic rather than as JSX.
 *
 * The tree arrives from `GET /runs/{id}/trace` already nested. What is decided
 * here is what a reader is shown, and the decisions are the kind that fail
 * silently:
 *
 * - filtering must not leave a row pointing at a parent that is not on screen,
 *   so a filter keeps the ancestors of what it matched and marks them as
 *   context;
 * - "model call" is derived from the row carrying a prompt version, not from a
 *   list of operation names, so a new prompt-bearing span cannot be silently
 *   left out of the model view;
 * - a price of zero is only a price when something says where it came from, so
 *   the split between billed and estimated is kept rather than summed.
 *
 * Plain `.js` on purpose: `node` imports it directly in the test suite.
 */

export const SPAN_FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'model', label: 'Model calls' },
  { id: 'problems', label: 'Problems' },
];

/** Label copy per operation. A new operation shows its own name, not a blank. */
const OPERATION_COPY = {
  run: 'Run',
  model_call: 'Extract claims',
  audit: 'Audit coverage',
  repair: 'Repair coverage',
  verify_claim: 'Verify claim',
  reuse_evidence: 'Reuse stored evidence',
  existing_sources: 'Check stored sources',
  search: 'Search',
  extract: 'Extract page',
  judge_evidence: 'Judge evidence',
};

export function spanLabel(row) {
  const operation = String(row?.operation || '');
  if (OPERATION_COPY[operation]) return OPERATION_COPY[operation];
  return operation ? operation.replaceAll('_', ' ') : 'operation';
}

/**
 * A span that asked a model something.
 *
 * `prompt_version` is the durable marker Bifrost calls are given. Judging by
 * operation name instead would mean the next stage that calls a model is
 * invisible in the one view built to show where the money went.
 */
export function isModelSpan(row) {
  return Boolean(String(row?.prompt_version || '').trim());
}

/** A span worth investigating: it failed, or it wanted the network and missed. */
export function isProblemSpan(row) {
  return Boolean(String(row?.error_type || '').trim()) || row?.cache_status === 'miss';
}

export function matchesFilter(row, filter) {
  if (filter === 'model') return isModelSpan(row);
  if (filter === 'problems') return isProblemSpan(row);
  return true;
}

function prune(node, keep) {
  if (!node || typeof node !== 'object') return null;
  const children = (Array.isArray(node.children) ? node.children : [])
    .map((child) => prune(child, keep))
    .filter(Boolean);
  const self = keep(node);
  if (!self && !children.length) return null;
  return { node, children, self };
}

/** How many spans the tree holds, and how many of them the filter matches. */
export function countSpans(tree, filter = 'all') {
  const matcher = (row) => matchesFilter(row, filter);
  let total = 0;
  let matching = 0;
  const walk = (node) => {
    if (!node) return;
    total += 1;
    if (matcher(node)) matching += 1;
    (node.children || []).forEach(walk);
  };
  walk(tree);
  return { total, matching };
}

/**
 * Depth-first rows, parents before children, siblings in `ordinal` order.
 *
 * A row with `context: true` is on screen only because something beneath it
 * matched. Saying so is the difference between "the run did this" and "this is
 * the branch the problem lives in".
 */
export function flattenTrace(tree, filter = 'all') {
  const matcher = (row) => matchesFilter(row, filter);
  const pruned = prune(tree, matcher);
  if (!pruned) return [];
  const rows = [];
  const walk = (branch, depth) => {
    const row = { ...branch.node, depth, context: !branch.self };
    delete row.children;
    rows.push(row);
    branch.children.forEach((child) => walk(child, depth + 1));
  };
  walk(pruned, 0);
  return rows;
}

/** Milliseconds as a duration a person can compare at a glance. */
export function formatDuration(value) {
  const ms = Number(value);
  if (!Number.isFinite(ms) || ms <= 0) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds - minutes * 60)}s`;
}

/** Token counts, compact enough for a table cell. */
export function formatTokens(value) {
  const tokens = Number(value) || 0;
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 10_000) return `${Math.round(tokens / 1000)}k`;
  return String(tokens);
}

/**
 * The second line of a span row: what it touched, and anything unusual.
 *
 * Ordered by what a reader needs first. A requested/resolved mismatch means
 * Bifrost fell back, so it leads; an error is next; a cache state and a prompt
 * version follow.
 */
export function spanDetail(row) {
  const parts = [];
  const requested = String(row?.requested_model || '');
  const resolved = String(row?.resolved_model || '');
  if (requested && resolved && requested !== resolved) {
    parts.push(`${requested} → ${resolved}`);
  } else if (resolved || requested) {
    parts.push(resolved || requested);
  }
  const error = String(row?.error_type || '');
  if (error) parts.push(`error: ${error}`);
  const tool = String(row?.tool_name || '');
  if (tool) {
    const args = row?.tool_arguments || {};
    const target = args.query || args.url || args.targets;
    parts.push(target ? `${tool} · ${String(target).slice(0, 90)}` : tool);
  }
  const cache = String(row?.cache_status || '');
  if (cache && cache !== 'live') parts.push(cache);
  const prompt = String(row?.prompt_version || '');
  if (prompt) parts.push(prompt);
  const decision = String(row?.decision_result || '');
  if (decision) parts.push(decision);
  if (Number(row?.attempt) > 1) parts.push(`attempt ${row.attempt}`);
  return parts.join(' · ');
}

/** The tone of a span's dot: a problem is loud, context is quiet. */
export function spanTone(row, { context = false } = {}) {
  if (String(row?.error_type || '').trim()) return 'danger';
  if (row?.cache_status === 'snapshot' || row?.cache_status === 'miss') return 'warn';
  if (context) return 'muted';
  if (row?.cache_status === 'cached') return 'ok';
  return 'muted';
}

/**
 * A stage's cost, split by where the number came from.
 *
 * `cost_source: none` means nobody priced the work, so a stage with no priced
 * span prints an em dash rather than `$0`. The span counts are what make that
 * distinction possible: a gateway that reported `0.0` did price its calls, and
 * that zero is a fact, not a gap.
 */
export function costCell(bucket) {
  return {
    billed: Number(bucket?.gateway_usd) || 0,
    estimated: Number(bucket?.estimated_usd) || 0,
    billedSpans: Number(bucket?.gateway_spans) || 0,
    estimatedSpans: Number(bucket?.catalogue_spans) || 0,
    unpriced: Number(bucket?.unpriced_spans) || 0,
  };
}

/** One span's price, and whether anything actually priced it. */
export function spanCost(row) {
  const source = String(row?.cost_source || 'none');
  return {
    priced: source === 'gateway' || source === 'catalogue',
    value: Number(row?.cost_usd) || 0,
  };
}
