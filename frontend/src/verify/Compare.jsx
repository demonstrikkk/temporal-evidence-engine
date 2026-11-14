/**
 * Compare models — a matrix of what this deployment actually measured.
 *
 * Two datasets meet here. `model_calls` knows calls, tokens, cost, latency and
 * failures. `answers` joined to `claims` knows the verdict mix of the text a
 * model wrote. Neither is a benchmark, and the page refuses to dress them up as
 * one: a supported share is a verdict mix, not a quality score, and a registry
 * row with no calls reports "not measured" instead of a row of zeros.
 *
 * That is the whole point of the layout. Columns are facts, every unmeasured
 * cell is an em dash, and the provenance notes sit below the table rather than
 * in a tooltip nobody opens.
 */
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import useResource from '../hooks/useResource.js';
import { ago, statusMeta } from './model.js';
import { Empty, SkeletonBlock } from './ui.jsx';
import {
  VERDICT_ORDER,
  evidenceLabel,
  filterRows,
  formatCount,
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
} from './compareModel.js';

const MODES = [
  { id: 'measured', label: 'Evidence' },
  { id: 'all', label: 'All rows' },
];

const NOTE_LABELS = {
  calls: 'What a call is',
  cost: 'Cost',
  verdicts: 'Verdict mix',
  attribution: 'Attribution',
  pasted: 'Pasted answers',
  registry: 'Registry rows',
};

function asNumber(value) {
  return value === null || value === undefined ? '—' : formatCount(value);
}

function SortHeader({ column, label, sort, onSort, numeric = false }) {
  const active = sort.key === column;
  return (
    <th
      scope="col"
      className={numeric ? 'cmp-num' : undefined}
      aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <button type="button" className="cmp-sort" onClick={() => onSort(column)}>
        {label}
        <span aria-hidden="true">{active ? (sort.dir === 'asc' ? '↑' : '↓') : '·'}</span>
      </button>
    </th>
  );
}

function Mix({ row }) {
  const mix = verdictMix(row);
  if (!row.answers) return <span className="cmp-muted">no scans</span>;
  if (!row.settled) {
    return (
      <span className="cmp-muted">
        {row.pending} pending claim{row.pending === 1 ? '' : 's'}
      </span>
    );
  }
  return (
    <div className="cmp-mix">
      <span className="cmp-bar" aria-hidden="true">
        {mix.map((segment) =>
          segment.share > 0 ? (
            <span
              key={segment.id}
              className={`cmp-seg is-${segment.id}`}
              style={{ width: `${segment.share * 100}%` }}
            />
          ) : null,
        )}
      </span>
      <span className="cmp-mix-text" aria-hidden="true">
        {mix.map((segment) => segment.count).join(' · ')}
      </span>
      <span className="sr-only">
        {mix
          .map((segment) => `${segment.count} ${segment.id}`)
          .join(', ')}
      </span>
    </div>
  );
}

function ModelCell({ row }) {
  const pasted = isPasted(row);
  const registry = isRegistry(row);
  const parts = [];
  if (!registry && !pasted) parts.push(evidenceLabel(row));
  if (isMeasured(row) && row.last_call_at) parts.push(`last call ${ago(row.last_call_at)}`);
  if (row.last_verified_at) parts.push(`last verified ${ago(row.last_verified_at)}`);
  const subject = pasted
    ? `${row.answers} answer${row.answers === 1 ? '' : 's'} · author not recorded`
    : parts.filter(Boolean).join(' · ') || evidenceLabel(row);
  return (
    <td>
      <span className={pasted ? 'cmp-model is-group' : 'cmp-model'} title={row.model}>
        {pasted ? 'pasted answers' : row.model}
      </span>
      <span className="cmp-sub">
        {pasted ? <span className="cmp-badge">no model recorded</span> : null}
        {registry ? <span className="cmp-badge">registry</span> : null}
        {subject}
      </span>
    </td>
  );
}

function Row({ row }) {
  const classes = ['cmp-row'];
  if (isRegistry(row)) classes.push('is-registry');
  if (isPasted(row)) classes.push('is-pasted');
  return (
    <tr className={classes.join(' ')}>
      <ModelCell row={row} />
      <td>
        <span className="cmp-company">{vendorLabel(row)}</span>
        {row.privacy ? <span className="cmp-sub">data: {row.privacy}</span> : null}
      </td>
      <td>
        <span className="cmp-route">{row.api}</span>
        {row.observed ? (
          <span
            className={`cmp-sub is-observed-${observedTone(row.observed)}`}
            title="The last time someone saw this route work"
          >
            {row.observed}
          </span>
        ) : null}
      </td>
      {isPasted(row) ? (
        // Five em dashes would read as a model with missing data. The ledger
        // columns are about a model's calls, and this group has no model, so it
        // says so once instead of five times.
        <td className="cmp-none" colSpan={5}>
          no call ledger rows — the answer was pasted, so no model ran here
        </td>
      ) : (
        <>
          <td className="cmp-num">{asNumber(row.calls)}</td>
          <td className="cmp-num">
            {row.calls === null ? (
              '—'
            ) : (
              <>
                <span>
                  {formatTokens(row.input_tokens)} / {formatTokens(row.output_tokens)}
                </span>
                <span className="cmp-sub">in / out</span>
              </>
            )}
          </td>
          <td className="cmp-num">
            {row.calls === null ? (
              '—'
            ) : (
              <>
                <span className={row.priced_calls ? undefined : 'cmp-muted'}>
                  {row.priced_calls ? formatUsd(row.avg_cost_usd) : 'unpriced'}
                </span>
                <span className="cmp-sub">
                  {row.priced_calls} of {row.calls} priced
                </span>
              </>
            )}
          </td>
          <td className="cmp-num">
            {row.avg_latency_ms === null ? (
              <span className="cmp-muted">{row.calls === null ? '—' : 'not timed'}</span>
            ) : (
              <>
                <span>{formatLatency(row.avg_latency_ms)}</span>
                <span className="cmp-sub">
                  {row.timed_calls} of {row.calls} timed
                </span>
              </>
            )}
          </td>
          <td className="cmp-num">
            {row.error_rate === null ? (
              '—'
            ) : (
              <>
                <span>{rateLabel(row.error_rate)}</span>
                <span className="cmp-sub">
                  {row.error_calls} of {row.calls}
                </span>
              </>
            )}
          </td>
        </>
      )}
      <td>
        <Mix row={row} />
        {row.answers ? (
          <span className="cmp-sub">
            {row.answers} answer{row.answers === 1 ? '' : 's'} · {row.claims} claim
            {row.claims === 1 ? '' : 's'} · {rateLabel(row.supported_rate)} supported
          </span>
        ) : null}
      </td>
      <td className="cmp-num">
        {row.answers ? (
          <>
            <span>{asNumber(row.due)}</span>
            <span className="cmp-sub">{row.time_sensitive} time-sensitive</span>
          </>
        ) : (
          <span className="cmp-muted">—</span>
        )}
      </td>
    </tr>
  );
}

export default function Compare() {
  const { data, status, error, refetch, isStale } = useResource(
    (o) => api.compare.models(o),
    [],
  );
  const [mode, setMode] = useState('measured');
  const [sort, setSort] = useState({ key: 'evidence', dir: 'desc' });

  const rows = data?.models || [];
  const totals = data?.totals || {};
  const notes = data?.notes || {};

  const evidence = useMemo(() => rows.filter((row) => !isRegistry(row)).length, [rows]);
  const visible = useMemo(
    () => sortRows(filterRows(rows, mode), sort.key, sort.dir),
    [rows, mode, sort],
  );

  function onSort(key) {
    setSort((current) =>
      current.key === key
        ? { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: key === 'model' ? 'asc' : 'desc' },
    );
  }

  const loading = status === 'loading' && !data;
  const empty = !loading && !error && visible.length === 0;

  return (
    <div className="space-y-6 animate-fade-up">
      <header className="page-head">
        <div>
          <p className="eyebrow">Measurement</p>
          <h1 className="page-title">
            Compare <em>models</em>
          </h1>
          <p className="page-lede">
            Every recorded call and every scanned answer, grouped by the model that answered.
            Cost, latency and failures come from the call ledger; the verdict mix comes from
            claims joined through the answer&rsquo;s recorded model. Nothing on this page is a
            benchmark score.
          </p>
        </div>
        <button type="button" className="btn-outline" onClick={refetch} disabled={loading}>
          {isStale ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>

      <dl className="strip-stats" aria-label="Comparison totals">
        <div>
          <dt>models measured</dt>
          <dd>{asNumber(data ? totals.measured_models : null)}</dd>
        </div>
        <div>
          <dt>recorded calls</dt>
          <dd>{asNumber(data ? totals.calls : null)}</dd>
        </div>
        <div>
          <dt>avg cost / call</dt>
          <dd>{data ? formatUsd(totals.avg_cost_usd) : '—'}</dd>
        </div>
        <div>
          <dt>supported share</dt>
          <dd>{data ? rateLabel(totals.supported_rate) : '—'}</dd>
        </div>
      </dl>

      <p className="cmp-lede-sum">
        {data
          ? `${formatUsd(totals.cost_usd)} recorded across ${asNumber(
              totals.priced_calls,
            )} priced calls · ${asNumber(totals.answers)} scanned answers${
              totals.pasted_answers ? ` (${asNumber(totals.pasted_answers)} pasted)` : ''
            } · ${asNumber(totals.claims)} claims · ${asNumber(totals.due)} due for re-check`
          : 'Reading the call ledger and the claim register.'}
      </p>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="seg" role="group" aria-label="Which rows to show">
          {MODES.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setMode(item.id)}
              aria-pressed={mode === item.id}
            >
              {item.label}
              <span className="count">
                {item.id === 'measured' ? evidence : rows.length}
              </span>
            </button>
          ))}
        </div>
        <p className="cmp-hint" id="cmp-order">
          {orderLabel(sort)}. A row with no measurement on the sorted column stays below the
          measured rows either way; pasted answers sit below the models, and catalogue entries
          stay last.
        </p>
      </div>

      {loading ? <SkeletonBlock lines={8} /> : null}

      {error && !data ? (
        <div className="surface p-4 text-[12px] text-danger" role="alert">
          The comparison could not be read: {error.message}
          <button type="button" className="btn-outline btn-xs ml-3" onClick={refetch}>
            Try again
          </button>
        </div>
      ) : null}

      {empty ? (
        <Empty title="Nothing measured yet">
          The matrix fills itself from the call ledger and from scanned answers. Run a check from
          the <Link className="underline" to="/playground">playground</Link> or the{' '}
          <Link className="underline" to="/check">answer checker</Link> — the model that wrote the
          answer is recorded with it, so its verdicts appear here.
        </Empty>
      ) : null}

      {visible.length ? (
        <div className="cmp-wrap">
          <table className="cmp" aria-describedby="cmp-order">
            <colgroup>
              <col className="cmp-col-model" />
              <col className="cmp-col-vendor" />
              <col className="cmp-col-api" />
              <col className="cmp-col-calls" />
              <col className="cmp-col-tokens" />
              <col className="cmp-col-cost" />
              <col className="cmp-col-latency" />
              <col className="cmp-col-errors" />
              <col className="cmp-col-verdict" />
              <col className="cmp-col-due" />
            </colgroup>
            <caption className="sr-only">
              Models, the provider and API they run on, recorded calls, tokens, average cost per
              call, average latency, failure rate, verdict mix and claims due for re-check.
            </caption>
            <thead>
              <tr>
                <SortHeader column="model" label="model" sort={sort} onSort={onSort} />
                <th scope="col">company / vendor</th>
                <th scope="col">api</th>
                <SortHeader column="calls" label="calls" sort={sort} onSort={onSort} numeric />
                <th scope="col" className="cmp-num">
                  tokens in / out
                </th>
                <SortHeader column="cost" label="avg cost / call" sort={sort} onSort={onSort} numeric />
                <SortHeader column="latency" label="latency" sort={sort} onSort={onSort} numeric />
                <th scope="col" className="cmp-num">
                  errors
                </th>
                <SortHeader
                  column="supported"
                  label="verdict mix"
                  sort={sort}
                  onSort={onSort}
                />
                <SortHeader column="due" label="due" sort={sort} onSort={onSort} numeric />
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <Row key={`${row.origin}:${row.model}`} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {visible.length ? (
        <ul className="cmp-legend" aria-label="Verdict colours in the mix bars">
          {VERDICT_ORDER.map((id) => (
            <li key={id}>
              <span className={`cmp-key is-${id}`} aria-hidden="true" />
              {statusMeta(id).label}
            </li>
          ))}
          <li className="cmp-legend-note">
            the counts in a mix cell follow this order
          </li>
        </ul>
      ) : null}

      {data ? (
        <section className="cmp-notes" aria-labelledby="cmp-notes-title">
          <h2 id="cmp-notes-title" className="eyebrow">
            What these numbers are
          </h2>
          <dl>
            {Object.entries(NOTE_LABELS)
              .filter(([key]) => notes[key])
              .map(([key, label]) => (
                <div key={key}>
                  <dt>{label}</dt>
                  <dd>{notes[key]}</dd>
                </div>
              ))}
          </dl>
          <p className="cmp-hint">
            Free-model registry rows come from the catalogue this deployment already publishes,
            not from a run. Supported share is the mix of verdicts over settled claims — a model
            that writes only safe trivialities scores high on it, so it is shown beside the claim
            count, never on its own.
          </p>
        </section>
      ) : null}
    </div>
  );
}
