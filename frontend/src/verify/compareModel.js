/**
 * The comparison matrix's arithmetic, kept out of the component.
 *
 * Everything here is a projection of numbers the API measured. The two rules
 * that matter:
 *
 * 1. **`null` is not zero.** A registry model has no calls, and an unpriced
 *    call has no cost — neither is the number 0. `null` renders as "not
 *    measured" and never sorts as the cheapest or the fastest.
 * 2. **Sorted rows keep registry rows below measured ones.** A catalogue entry
 *    is context, not evidence, and sorting by a metric it does not have must
 *    not lift it above a model that actually ran.
 */

export const VERDICT_ORDER = ['supported', 'changed', 'contradicted', 'unverifiable'];

export const SORTS = {
  evidence: 'evidence',
  model: 'model',
  calls: 'calls',
  cost: 'cost',
  latency: 'latency',
  supported: 'supported',
  due: 'due',
};

export function isMeasured(row) {
  return row?.origin === 'measured';
}

/** Pasted answers: real claims, no recorded author. A group, not a model. */
export function isPasted(row) {
  return row?.origin === 'pasted';
}

export function isRegistry(row) {
  return !isMeasured(row) && !isPasted(row);
}

/**
 * The table's bands: models, then the pasted group, then the catalogue.
 *
 * Pasted answers carry verdicts but no model, so they are evidence without
 * being a candidate: they rank below every model and above every catalogue
 * entry, whatever the sort key or direction.
 */
function rowRank(row) {
  if (isMeasured(row)) return 0;
  if (isPasted(row)) return 1;
  return 2;
}

/** A share as a whole percent, or an em dash when the denominator was zero. */
export function rateLabel(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return `${Math.round(Number(value) * 100)}%`;
}

/**
 * A dollar amount with enough decimals to tell sub-cent calls apart.
 *
 * Per-call cost on a small model is often $0.0002, and `toFixed(2)` would print
 * `$0.00` for every one of them — the column would claim the runs were free.
 */
export function formatUsd(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const amount = Number(value);
  if (amount === 0) return '$0.00';
  if (amount >= 1) return `$${amount.toFixed(2)}`;
  if (amount >= 0.01) return `$${amount.toFixed(4)}`;
  return `$${amount.toFixed(6)}`;
}

export function formatCount(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString('en-US');
}

export function formatTokens(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const amount = Number(value);
  if (amount < 1_000) return String(amount);
  if (amount < 1_000_000) {
    const short = amount / 1_000;
    return `${short >= 10 ? Math.round(short) : short.toFixed(1)}k`;
  }
  return `${(amount / 1_000_000).toFixed(1)}M`;
}

export function formatLatency(ms) {
  if (ms === null || ms === undefined || Number.isNaN(Number(ms))) return '—';
  const value = Number(ms);
  if (value < 1_000) return `${Math.round(value)} ms`;
  return `${(value / 1_000).toFixed(value < 10_000 ? 1 : 0)} s`;
}

/**
 * The verdict mix, as segments with counts and shares.
 *
 * Shares come from the API when it computed them; otherwise the settled count
 * is the denominator. `pending` is deliberately absent — it is not a verdict,
 * and drawing it in the same bar would make "still running" look like a result.
 */
export function verdictMix(row) {
  const settled = Number(row?.settled || 0);
  return VERDICT_ORDER.map((id) => {
    const count = Number(row?.[id] || 0);
    const share = row?.[`${id}_rate`] ?? (settled ? count / settled : null);
    return { id, count, share };
  });
}

/** What the row can say about itself when there is no metric to show. */
export function evidenceLabel(row) {
  if (isPasted(row)) return 'pasted answers — model not recorded';
  if (isRegistry(row)) return 'catalogue entry — no calls recorded';
  const answers = Number(row.answers || 0);
  const calls = row.calls;
  if (!answers && !calls) return 'no calls, no scans';
  if (!answers) return 'no scanned answers yet';
  return `${answers} scanned answer${answers === 1 ? '' : 's'}`;
}

function evidenceWeight(row) {
  if (isRegistry(row)) return null;
  return Number(row.calls || 0) + Number(row.claims || 0);
}

const ACCESSORS = {
  evidence: evidenceWeight,
  model: (row) => String(row.model || ''),
  calls: (row) => (isMeasured(row) ? row.calls : null),
  cost: (row) => (isMeasured(row) ? row.avg_cost_usd : null),
  latency: (row) => (isMeasured(row) ? row.avg_latency_ms : null),
  supported: (row) => (isMeasured(row) ? row.supported_rate : null),
  due: (row) => (isMeasured(row) ? row.due : null),
};
function comparable(value) {
  return value !== null && value !== undefined && !Number.isNaN(Number(value));
}

/**
 * Sort by one key, with unmeasured rows always last.
 *
 * The comparator is total: equal values fall back to the model id, so the table
 * does not reshuffle between renders when two rows tie.
 */
export function sortRows(rows, key = 'evidence', dir = 'desc') {
  const accessor = ACCESSORS[key] || ACCESSORS.evidence;
  const factor = dir === 'asc' ? 1 : -1;
  return [...(rows || [])].sort((a, b) => {
    // A printed group or a catalogue entry is not a model. Both stay below every
    // model row on every key and in both directions, so "cheapest" cannot be
    // won by a row that has never been compared on cost.
    const rankA = rowRank(a);
    const rankB = rowRank(b);
    if (rankA !== rankB) return rankA - rankB;

    const av = accessor(a);
    const bv = accessor(b);
    const an = key === 'model' ? !av : !comparable(av);
    const bn = key === 'model' ? !bv : !comparable(bv);
    if (an && bn) return String(a.model).localeCompare(String(b.model));
    if (an) return 1;
    if (bn) return -1;
    if (key === 'model') {
      const order = String(av).localeCompare(String(bv));
      return order === 0 ? 0 : order * factor;
    }
    if (Number(av) === Number(bv)) {
      return String(a.model).localeCompare(String(b.model));
    }
    return (Number(av) - Number(bv)) * factor;
  });
}

/** Every row that has evidence: models and pasted groups. Catalogues excluded. */
export function filterRows(rows, mode = 'measured') {
  if (mode === 'all') return rows || [];
  return (rows || []).filter((row) => !isRegistry(row));
}

/** Whether the observed string says the route worked when someone last tried. */
export function observedTone(observed) {
  const text = String(observed || '').trim().toLowerCase();
  if (!text) return 'none';
  if (text.startsWith('ok')) return 'ok';
  return 'partial';
}

/** The company/API as recorded, without inventing a display name for it. */
export function vendorLabel(row) {
  if (isPasted(row)) return 'not recorded';
  const providers = Array.isArray(row?.providers) ? row.providers.filter(Boolean) : [];
  if (providers.length) return providers.join(', ');
  return row?.vendor || 'not recorded';
}

/** What each sort key is, in the words a reader needs to hear it. */
const SORT_LABELS = {
  evidence: 'evidence',
  model: 'model id',
  calls: 'recorded calls',
  cost: 'average cost per call',
  latency: 'average latency',
  supported: 'supported share',
  due: 'claims due for re-check',
};

/**
 * The order the table is in, as a sentence.
 *
 * `evidence` is the default and belongs to no single column — it ranks rows by
 * how much was measured — so no `aria-sort` can announce it. Rather than
 * pretend the table is sorted by a column it is not, the order is stated in
 * text and the table is described by it from the first focus.
 */
export function orderLabel(sort) {
  const key = SORT_LABELS[sort?.key] ? sort.key : 'evidence';
  if (key === 'evidence') return 'Ordered by evidence, most measured first';
  return `Ordered by ${SORT_LABELS[key]}, ${sort?.dir === 'asc' ? 'ascending' : 'descending'}`;
}
