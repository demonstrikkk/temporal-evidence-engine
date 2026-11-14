/**
 * Pure presentation logic for the verifier.
 *
 * Kept out of `ui.jsx` on purpose: this file has no JSX, so plain `node` can
 * import it and the test suite can pin the decisions that are easy to get
 * quietly wrong — status vocabulary, span segmentation, event narration.
 */

export const STATUS = {
  supported: {
    label: 'Supported',
    cls: 'text-ok border-ok/35 bg-ok-soft',
    dot: 'bg-ok',
    tone: 'ok',
    note: 'A locatable passage states the same fact.',
  },
  changed: {
    label: 'Changed',
    cls: 'text-info border-info/35 bg-info-soft',
    dot: 'bg-info',
    tone: 'info',
    note: 'A later, settled source states a replacement value.',
  },
  contradicted: {
    label: 'Contradicted',
    cls: 'text-danger border-danger/35 bg-danger-soft',
    dot: 'bg-danger',
    tone: 'danger',
    note: 'A locatable passage refutes the claim.',
  },
  unverifiable: {
    label: 'Unverifiable',
    cls: 'text-muted border-rule-2 bg-warm/60',
    dot: 'bg-muted',
    tone: 'muted',
    note: 'No source establishes or refutes it. This is not "false".',
  },
  pending: {
    label: 'Pending',
    cls: 'text-muted border-rule-2 bg-paper-2',
    dot: 'bg-rule-2',
    tone: 'muted',
    note: 'Not researched yet.',
  },
};

export const VERDICTS = ['supported', 'changed', 'contradicted', 'unverifiable'];

/** Underline/fill used when a claim is marked inside the answer text. */
export const MARK = {
  supported: 'border-ok/60 bg-ok-soft',
  changed: 'border-info/60 bg-info-soft',
  contradicted: 'border-danger/60 bg-danger-soft',
  unverifiable: 'border-rule-2 bg-warm/70',
  pending: 'border-rule-2 bg-paper-3',
};

export const REASON = {
  supported_by_source: 'supported by source',
  value_changed_after_generation: 'value changed after generation',
  contradicted_by_source: 'contradicted by source',
  no_source: 'no locatable source',
  no_entailment: 'sources found, none establishes it',
  temporal_ambiguity: 'event dates unknown or conflicting',
  conflicting_evidence: 'sources disagree',
  missing_event_date: 'event date missing',
  scope_mismatch: 'values differ in scope (currency, period, per unit)',
  extraction_failed: 'extraction failed',
  generation_time_unknown: 'generation time unknown',
  no_claims: 'no checkable claims',
};

export const RUN_STATE = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  partial: 'Partial',
  failed: 'Failed',
};

export const TERMINAL_RUN_STATES = new Set(['completed', 'partial', 'failed']);

export function statusMeta(status) {
  return STATUS[status] || STATUS.pending;
}

export function reasonLabel(code) {
  if (!code) return 'no reason code';
  return REASON[code] || String(code).replaceAll('_', ' ');
}

/** Counts per status, plus a `total` that is always the row count. */
export function claimCounts(claims) {
  const out = { supported: 0, changed: 0, contradicted: 0, unverifiable: 0, pending: 0 };
  for (const claim of claims || []) {
    const key = Object.hasOwn(out, claim?.status) ? claim.status : 'pending';
    out[key] += 1;
  }
  out.total = (claims || []).length;
  return out;
}

/**
 * The counters a run view shows: derived from the claims, then overridden by
 * what the store recorded.
 *
 * The recorded counters only carry statuses that occurred, so a run with no
 * abstention has no `unverifiable` key. Reading it straight from the payload
 * printed "undefined abstained" and "no claims" on a real completed run. Every
 * verdict key exists here; a recorded count still wins where it exists.
 */
export function runCounts(claims, counters) {
  const derived = claimCounts(claims);
  if (!counters || typeof counters !== 'object') return derived;
  return { ...derived, ...counters };
}

/**
 * Split the answer into plain and claim-backed segments.
 *
 * Rules that matter for a reader:
 * - a span must be inside the answer; a stale offset is dropped, never clamped;
 * - the earliest span wins an overlap, so a later bad span cannot swallow an
 *   earlier correct one;
 * - the returned segments always reassemble the answer byte-for-byte, which is
 *   what makes "the claim is in this sentence" trustworthy.
 */
export function buildSegments(answer, claims) {
  const text = String(answer ?? '');
  const spans = (claims || [])
    .filter((claim) => {
      const start = claim?.span_start;
      const end = claim?.span_end;
      return (
        Number.isInteger(start) &&
        Number.isInteger(end) &&
        start >= 0 &&
        end > start &&
        end <= text.length
      );
    })
    .sort((a, b) => a.span_start - b.span_start || b.span_end - a.span_end);

  const segments = [];
  let cursor = 0;
  for (const claim of spans) {
    if (claim.span_start < cursor) continue;
    if (claim.span_start > cursor) {
      segments.push({ text: text.slice(cursor, claim.span_start), claim: null });
    }
    segments.push({ text: text.slice(claim.span_start, claim.span_end), claim });
    cursor = claim.span_end;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), claim: null });
  return segments;
}

const EVENT_COPY = {
  'run.started': ['Run started', (p) => `rule ${p.rule_version || '—'} · cache ${p.cache_mode || '—'}`],
  'claims.extracted': ['Claims extracted', (p) => `${p.count ?? 0} checkable claim${p.count === 1 ? '' : 's'}`],
  'claim.started': ['Checking claim', (p) => p.claim || ''],
  'claim.settled': ['Verdict recorded', (p) => reasonLabel(p.reason_code)],
  'claim.failed': ['Claim could not be verified', (p) => p.error || ''],
  'search.done': ['Search returned', (p) => `${p.hits ?? 0} result${p.hits === 1 ? '' : 's'} · “${p.query || ''}”`],
  'search.failed': ['Search failed', (p) => `${p.error || 'unknown error'}${p.query ? ` · “${p.query}”` : ''}`],
  'research.planned': ['Research planned', (p) =>
    `${p.queries?.length ?? 0} quer${(p.queries?.length ?? 0) === 1 ? 'y' : 'ies'} for ${p.claims ?? 0} claim${(p.claims ?? 0) === 1 ? '' : 's'}`],
  'research.budget_exhausted': ['Query plan refused', (p) =>
    `${p.queries_dropped ?? 0} question${(p.queries_dropped ?? 0) === 1 ? '' : 's'} dropped by ${p.budget_mode || 'the budget mode'}`],
  'claim.source_reused': ['Stored source reused', (p) =>
    `${p.sources ?? 0} page${(p.sources ?? 0) === 1 ? '' : 's'} already held were settled before searching`],
  'claim.evidence_reused': ['Evidence reused', (p) =>
    `${p.evidence_count ?? 0} evidence row${(p.evidence_count ?? 0) === 1 ? '' : 's'} copied from a prior run`],
  'claim.recovered': ['Claim recovered', (p) => p.claim || ''],
  'claim.dropped': ['Claim dropped', (p) => `${p.stage || 'extraction'}${p.reason ? ` — ${p.reason}` : ''}`],
  'source.stored': ['Source stored', (p) => p.url || ''],
  'source.failed': ['Source failed', (p) => `${p.url || ''}${p.error ? ` — ${p.error}` : ''}`],
  'evidence.settled': ['Evidence assessed', (p) =>
    p.settled ? `${p.relationship} settled` : `${p.relationship} not settled${p.reason ? ` — ${p.reason}` : ''}`],
  'evidence.skipped': ['Evidence skipped', (p) => p.reason || ''],
  'model.failed': ['Model call failed', (p) => `${p.stage || 'model'}${p.error ? ` — ${p.error}` : ''}`],
  'model.snapshot': ['Snapshot replay', (p) =>
    `${p.stage || ''}${p.created_at ? ` · recorded ${shortDate(p.created_at)}` : ''}`],
  'run.failure': ['Stage reported a failure', (p) => `${p.stage || ''}${p.message ? ` — ${p.message}` : ''}`],
  'run.completed': ['Run completed', (p) => `${p.counters?.claims ?? 0} claims`],
  'run.failed': ['Run failed', (p) => p.error?.message || p.error?.code || 'no reason recorded'],
  'run.partial': ['Run finished partially', (p) =>
    p.error?.message || p.error?.code
    || `${p.failures?.length ?? 0} reported failure${p.failures?.length === 1 ? '' : 's'}`],
};

const EVENT_TONE = {
  'run.failure': 'danger',
  'run.failed': 'danger',
  'model.failed': 'danger',
  'claim.failed': 'danger',
  'source.failed': 'warn',
  'search.failed': 'warn',
  'run.partial': 'warn',
  'evidence.skipped': 'warn',
  'research.budget_exhausted': 'warn',
  'claim.dropped': 'warn',
  'run.completed': 'ok',
  'claim.settled': 'ok',
  'source.stored': 'ok',
  'claims.extracted': 'ok',
  'claim.recovered': 'ok',
  'claim.source_reused': 'ok',
};

export function describeEvent(event) {
  const type = String(event?.event_type || event?.event || 'event');
  const payload = event?.payload && typeof event.payload === 'object' ? event.payload : {};
  const copy = EVENT_COPY[type];
  const [label, detail] = copy ? [copy[0], copy[1](payload)] : [type, ''];
  return { type, label, detail: String(detail || ''), tone: EVENT_TONE[type] || 'muted' };
}

/** A run is "live" while a worker may still be writing to it. */
export function isLive(status) {
  return status === 'queued' || status === 'running';
}

/**
 * What the research stage actually did, read from the run's own events.
 *
 * Tavily is a mandatory part of the product and "we integrate Tavily" is a
 * claim like any other, so the counts come from the events the worker emitted
 * rather than from a re-count performed here. Pages are deduplicated by
 * revision: the same page settled against three claims is one stored source.
 */
export function researchSummary(events) {
  const list = Array.isArray(events) ? events : [];
  const empty = { plans: 0, planned: 0, stored: 0, reused: 0, failed: 0, budgetStops: 0, sources: [] };
  if (!list.length) return empty;

  const sources = new Map();
  const out = { ...empty, sources: [] };

  for (const event of list) {
    const type = String(event?.event_type || '');
    const payload = event?.payload && typeof event.payload === 'object' ? event.payload : {};
    if (type === 'research.planned') {
      out.plans += 1;
      out.planned += Array.isArray(payload.queries) ? payload.queries.length : 0;
    } else if (type === 'research.budget_exhausted') {
      out.budgetStops += 1;
    } else if (type === 'source.stored') {
      // One page settled against three claims is one stored source, so pages
      // are deduplicated by revision rather than counted per claim.
      const key = payload.revision_id || payload.url;
      if (key && !sources.has(key)) {
        sources.set(key, {
          url: String(payload.url || ''),
          chars: Number(payload.chars) || 0,
          publishedAt: payload.published_date || null,
          storedAt: event.created_at || null,
        });
      }
    } else if (type === 'source.failed') {
      out.failed += 1;
    } else if (type === 'claim.source_reused' || type === 'claim.evidence_reused') {
      out.reused += 1;
    }
  }

  out.stored = sources.size;
  out.sources = [...sources.values()];
  return out;
}

export function shortUrl(url, max = 52) {
  const text = String(url || '').replace(/^https?:\/\//, '').replace(/\/$/, '');
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

export function shortDate(value) {
  if (!value) return '—';
  const text = String(value);
  const day = text.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : text;
}

export function timeOnly(value) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value).slice(11, 19);
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function ago(value) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  const delta = Math.round((Date.now() - parsed.getTime()) / 1000);
  const future = delta < 0;
  const seconds = Math.abs(delta);
  let span;
  if (seconds < 60) span = `${seconds}s`;
  else if (seconds < 3600) span = `${Math.round(seconds / 60)}m`;
  else if (seconds < 86400) span = `${Math.round(seconds / 3600)}h`;
  else span = `${Math.round(seconds / 86400)}d`;
  // A recommended recheck is usually in the future; "0s ago" for next Tuesday
  // would read as overdue, which is the opposite of what the date means.
  return future ? `in ${span}` : `${span} ago`;
}

export function countLabel(value) {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat('en').format(value);
}

/**
 * Freshness is the age of the last verification, not a probability of truth.
 *
 * The backend owns the number: it decays the score by a half-life per
 * volatility class and sends both the score and that half-life with the claim.
 * These two functions only choose a word and a colour for it, so the bands are
 * presentation policy. They must never be quoted as calibration.
 */
const FRESHNESS_BANDS = [
  { at: 70, tone: 'ok', word: 'current' },
  { at: 40, tone: 'warn', word: 'ageing' },
  { at: 0, tone: 'danger', word: 'stale' },
];

export function freshnessTone(index) {
  if (index === null || index === undefined || !Number.isFinite(Number(index))) return 'muted';
  const value = Number(index);
  return (FRESHNESS_BANDS.find((band) => value >= band.at) || FRESHNESS_BANDS[2]).tone;
}

export function freshnessWord(index) {
  // A missing score is not a stale score: the claim was never verified, which
  // is a different statement from "verified long ago".
  if (index === null || index === undefined || !Number.isFinite(Number(index))) {
    return 'not verified';
  }
  const value = Number(index);
  return (FRESHNESS_BANDS.find((band) => value >= band.at) || FRESHNESS_BANDS[2]).word;
}

/** Half-life hours as a readable span: 720 -> "30 d", 24 -> "24 h". */
export function halfLifeLabel(hours) {
  const value = Number(hours);
  if (!Number.isFinite(value) || value <= 0) return '—';
  if (value >= 24 && value % 24 === 0) return `${value / 24} d`;
  if (value >= 1) return `${Math.round(value * 10) / 10} h`;
  return `${Math.round(value * 60)} min`;
}

/**
 * A recorded cost, never an estimate.
 *
 * `null` is not `$0.0000`: a call with no cost recorded is missing data, and
 * printing a zero would make a gateway that reports nothing look free.
 */
export function formatCost(value) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  if (number === 0) return '$0';
  if (number < 0.01) return `$${number.toFixed(4)}`;
  return `$${number.toFixed(2)}`;
}

/**
 * The route a recorded call actually took: provider, then the model that
 * answered.
 *
 * `model` on a call row is what the caller asked for, because that is the cache
 * key. `resolved_model` is what the gateway used after routing — they differ
 * exactly when a fallback fired, which is the case this label must not
 * misreport: calling a local fallback `groq/openai/gpt-oss-20b` would make the
 * one panel that proves the gateway is real into the one that lies about it.
 */
export function gatewayRoute(call) {
  const provider = String(call?.provider || '').trim();
  const model = String(call?.resolved_model || call?.model || '').trim();
  if (provider && model) return `${provider} · ${model}`;
  return provider || model || 'unrecorded route';
}

/** Events worth showing without expanding the rail; the rest stay visible too. */
export const MAJOR_EVENTS = new Set([
  'run.started',
  'claims.extracted',
  'source.stored',
  'source.failed',
  'claim.settled',
  'model.failed',
  'run.failure',
  'run.completed',
  'run.failed',
  'run.partial',
]);

/** True when an event reports something that went wrong. */
export function isProblem(event) {
  return ['danger', 'warn'].includes(describeEvent(event).tone);
}
