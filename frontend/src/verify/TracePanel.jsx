/**
 * The run as a clock, with the price attached to each span.
 *
 * The event rail answers "what happened, in order". This panel answers the
 * questions the rail cannot: what was slow, what ran at the same time, which
 * stage spent the tokens, and what a span actually did. It is a projection of
 * the same rows — every span names the audit event that reports it — so the two
 * cannot disagree.
 *
 * Bars are placed on the run's own clock (`started_at`/`ended_at`), not on the
 * order rows arrived. A zero-length span is a hairline rather than a widened
 * bar, because a bar that is widened to be visible would claim work that did
 * not happen.
 *
 * Cost is split rather than summed. A gateway bill and a catalogue estimate are
 * different kinds of number, and adding them produces one number that is
 * neither.
 */
import { useEffect, useMemo, useState } from 'react';
import { api } from '../lib/api';
import { formatCost } from './model.js';
import {
  SPAN_FILTERS,
  costCell,
  countSpans,
  flattenTrace,
  formatDuration,
  formatOffset,
  formatTokens,
  slowestSpan,
  spanCost,
  spanDetail,
  spanLabel,
  spanTone,
  traceWindow,
  waterfallRows,
} from './traceModel.js';
import { SectionHeading, SkeletonBlock } from './ui.jsx';

const TONE_DOT = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  muted: 'bg-rule-2',
};

function Stat({ label, value, detail, tone = 'text-ink' }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] uppercase tracking-[0.14em] text-muted">{label}</p>
      <p className={`mt-0.5 font-mono text-[17px] leading-none ${tone}`}>{value}</p>
      <p className="mt-1 text-[10.5px] text-muted">{detail}</p>
    </div>
  );
}

function StageRow({ bucket }) {
  const cost = costCell(bucket);
  const problems = Number(bucket.errors) || 0;
  return (
    <tr className="border-b border-rule/60 last:border-0">
      <th scope="row" className="py-1.5 pr-2 text-left font-normal text-ink-2">
        {bucket.stage}
      </th>
      <td className="py-1.5 pr-2 text-right font-mono tabular-nums text-muted">
        {bucket.spans}
      </td>
      <td className="py-1.5 pr-2 text-right font-mono tabular-nums text-muted">
        {formatTokens(Number(bucket.input_tokens) + Number(bucket.output_tokens))}
      </td>
      <td className="py-1.5 pr-2 text-right font-mono tabular-nums text-ink-2">
        {cost.billedSpans ? formatCost(cost.billed) : '—'}
      </td>
      <td className="py-1.5 pr-2 text-right font-mono tabular-nums text-ink-2">
        {cost.estimatedSpans ? formatCost(cost.estimated) : '—'}
      </td>
      <td
        className={`py-1.5 text-right font-mono tabular-nums ${
          problems ? 'text-danger' : 'text-muted'
        }`}
      >
        {problems || '—'}
      </td>
    </tr>
  );
}

/** What the span did, in the order an auditor asks for it. */
function SpanFacts({ row }) {
  const cost = spanCost(row);
  const facts = [
    ['stage', row.stage],
    ['operation', row.operation],
    ['started', row.started_at ? formatOffset(row.offsetMs) : '—'],
    ['duration', formatDuration(row.durationMs ?? row.latency_ms)],
    [
      'model',
      row.requested_model && row.resolved_model && row.requested_model !== row.resolved_model
        ? `${row.requested_model} → ${row.resolved_model}`
        : row.resolved_model || row.requested_model || '—',
    ],
    ['provider', row.provider || '—'],
    [
      'tokens',
      Number(row.input_tokens) || Number(row.output_tokens)
        ? `${formatTokens(row.input_tokens)} in · ${formatTokens(row.output_tokens)} out`
        : 'not recorded',
    ],
    ['cost', cost.priced ? `${formatCost(cost.value)} (${row.cost_source})` : 'unpriced'],
    ['cache', row.cache_status || '—'],
    ['prompt', row.prompt_version || '—'],
    ['tool', row.tool_name || '—'],
    ['error', row.error_type || '—'],
    ['decision', row.decision_result || '—'],
  ];
  const args = row.tool_arguments && Object.keys(row.tool_arguments).length
    ? JSON.stringify(row.tool_arguments)
    : '';
  return (
    <div className="trace-facts">
      <dl>
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd title={String(value)}>{String(value)}</dd>
          </div>
        ))}
      </dl>
      {args ? <p className="trace-args">{args}</p> : null}
      {row.claim_id ? (
        <p className="trace-args">claim {String(row.claim_id).slice(0, 8)}</p>
      ) : null}
    </div>
  );
}

function SpanBar({ row, expanded, onToggle }) {
  const tone = spanTone(row, { context: row.context });
  const detail = spanDetail(row);
  const cost = spanCost(row);
  const width = row.widthPct === null ? null : Math.max(0, row.widthPct);
  const barTitle = [
    spanLabel(row),
    row.stage,
    row.started_at ? `starts ${formatOffset(row.offsetMs)}` : 'start not recorded',
    `lasts ${formatDuration(row.durationMs ?? row.latency_ms)}`,
    cost.priced ? formatCost(cost.value) : 'unpriced',
    row.error_type ? `error: ${row.error_type}` : '',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <li
      className={`trace-row${expanded ? ' is-open' : ''}${row.context ? ' is-context' : ''}`}
      data-tone={tone}
    >
      <button
        type="button"
        className="trace-row-btn"
        aria-expanded={expanded}
        onClick={onToggle}
      >
        <span className="trace-lab" style={{ paddingLeft: `${Math.min(row.depth, 5) * 11}px` }}>
          <span className={`trace-dot ${TONE_DOT[tone]}`} aria-hidden="true" />
          <span className="trace-name">
            <span className="trace-op">{spanLabel(row)}</span>
            <span className="trace-stage">{row.stage}</span>
            {detail ? (
              <span className="trace-detail" title={detail}>
                {detail}
              </span>
            ) : null}
          </span>
        </span>

        <span className="trace-track" title={barTitle} aria-hidden="true">
          {width === null ? (
            <span className="trace-nobar">no clock</span>
          ) : (
            <span
              className="trace-bar"
              data-stage={row.stage}
              style={{ left: `${row.startPct}%`, width: `${width}%` }}
            />
          )}
        </span>

        <span className="trace-nums">
          <span className="trace-dur">{formatDuration(row.durationMs ?? row.latency_ms)}</span>
          <span className="trace-money">{cost.priced ? formatCost(cost.value) : '—'}</span>
        </span>
      </button>
      {expanded ? <SpanFacts row={row} /> : null}
    </li>
  );
}

function Axis({ window: win }) {
  const ticks = [0, 25, 50, 75, 100];
  if (!win?.totalMs) return null;
  return (
    <div className="trace-axis" aria-hidden="true">
      <span className="trace-axis-gutter" />
      <div className="trace-axis-track">
        {ticks.map((pct) => (
          <span
            key={pct}
            className="trace-tick"
            style={{ left: `${pct}%` }}
          >
            {pct === 0 ? '0s' : formatDuration((win.totalMs * pct) / 100)}
          </span>
        ))}
      </div>
      <span className="trace-axis-gutter" />
    </div>
  );
}

export default function TracePanel({ runId, live }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('all');
  const [openId, setOpenId] = useState(null);

  useEffect(() => {
    let alive = true;
    if (live) {
      setData(null);
      return () => {
        alive = false;
      };
    }
    api.verify
      .trace(runId)
      .then((payload) => {
        if (!alive) return;
        setData(payload);
        setError(null);
      })
      .catch((err) => {
        if (alive) setError(err);
      });
    return () => {
      alive = false;
    };
  }, [runId, live]);

  const rows = useMemo(
    () => flattenTrace(data?.tree, filter),
    [data?.tree, filter],
  );
  // The axis is measured over every span, so filtering a chart never rescales
  // the bars that remain. A bar whose length changed because of a filter would
  // be a chart that lies about the run.
  const allRows = useMemo(() => flattenTrace(data?.tree, 'all'), [data?.tree]);
  const win = useMemo(() => traceWindow(allRows), [allRows]);
  const bars = useMemo(() => waterfallRows(rows, win), [rows, win]);
  const slowest = useMemo(() => slowestSpan(allRows, win), [allRows, win]);

  // One pass per filter rather than one per render: the chips show how much
  // each view holds, and recomputing that on every render is work nobody asked
  // for.
  const counts = useMemo(() => {
    const out = {};
    for (const entry of SPAN_FILTERS) out[entry.id] = countSpans(data?.tree, entry.id);
    return out;
  }, [data?.tree]);

  if (!data) {
    return (
      <section className="surface p-4" aria-label="Execution trace">
        <SectionHeading>Execution trace</SectionHeading>
        {error ? (
          <p className="mt-2 text-[11.5px] text-muted">
            The trace could not be loaded for this run.{' '}
            {error.message ? <span className="font-mono">{error.message}</span> : null}
          </p>
        ) : live ? (
          <div className="mt-3">
            <SkeletonBlock lines={3} />
          </div>
        ) : null}
      </section>
    );
  }

  const summary = data.summary || { totals: {}, stages: [] };
  const totals = summary.totals || {};

  if (!data.trace) {
    return (
      <section className="surface p-4" aria-label="Execution trace">
        <SectionHeading>Execution trace</SectionHeading>
        <p className="mt-2 text-[11.5px] leading-relaxed text-muted">
          {data.note ||
            'This run was executed with tracing off, so it has no tree.'}
        </p>
      </section>
    );
  }

  return (
    <section className="surface p-4" aria-label="Execution trace">
      <SectionHeading
        aside={`${Number(totals.spans) || 0} span${
          Number(totals.spans) === 1 ? '' : 's'
        } · ${data.trace.status || 'unknown'}`}
      >
        Execution trace
      </SectionHeading>

      <div className="mt-3 grid grid-cols-2 gap-3 border-y border-rule py-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat
          label="Wall clock"
          value={win ? formatDuration(win.totalMs) : '—'}
          detail={win ? 'first start to last end' : 'no span clock recorded'}
        />
        <Stat
          label="Billed"
          value={
            Number(totals.gateway_spans)
              ? formatCost(Number(totals.gateway_usd) || 0)
              : '—'
          }
          detail="reported by the gateway"
        />
        <Stat
          label="Estimated"
          value={
            Number(totals.catalogue_spans)
              ? formatCost(Number(totals.estimated_usd) || 0)
              : '—'
          }
          detail="from the price catalogue"
        />
        <Stat
          label="Unpriced"
          value={Number(totals.unpriced_spans) || 0}
          detail="spans with no price recorded"
          tone={Number(totals.unpriced_spans) ? 'text-warn' : 'text-ink'}
        />
        <Stat
          label="Problems"
          value={Number(totals.errors) || 0}
          detail={
            Number(totals.snapshots)
              ? `${totals.snapshots} replayed from cache`
              : 'failed or missed'
          }
          tone={Number(totals.errors) ? 'text-danger' : 'text-ink'}
        />
      </div>

      {slowest ? (
        <p className="mt-2.5 text-[11px] text-muted">
          Slowest step{' '}
          <span className="text-ink-2">{spanLabel(slowest)}</span>{' '}
          <span className="font-mono">{formatDuration(slowest.durationMs)}</span>
          {slowest.share ? (
            <span className="font-mono"> · {Math.round(slowest.share * 100)}% of wall clock</span>
          ) : null}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {SPAN_FILTERS.map((entry) => {
          const active = entry.id === filter;
          const matching = counts[entry.id]?.matching ?? 0;
          return (
            <button
              key={entry.id}
              type="button"
              aria-pressed={active}
              onClick={() => setFilter(entry.id)}
              className={`rounded-full border px-2 py-0.5 font-mono text-[10.5px] transition-colors duration-150 ${
                active
                  ? 'border-rule-2 bg-paper-2 text-ink'
                  : 'border-rule bg-paper text-muted hover:text-ink-2'
              }`}
            >
              {entry.label}
              {entry.id === 'all' ? '' : ` ${matching}`}
            </button>
          );
        })}
        <span className="ml-auto text-[10.5px] text-muted">
          {bars.length}
          {filter === 'all' ? '' : ` of ${counts.all?.total ?? bars.length}`} shown
          {win ? ' · click a row for its receipt' : ''}
        </span>
      </div>

      {win ? (
        <div className="trace-flow">
          <Axis window={win} />
          <ol className="trace-list">
            {bars.length ? (
              bars.map((row) => (
                <SpanBar
                  key={row.id || `${row.ordinal}-${row.depth}`}
                  row={row}
                  expanded={openId === (row.id || `${row.ordinal}-${row.depth}`)}
                  onToggle={() =>
                    setOpenId((current) =>
                      current === (row.id || `${row.ordinal}-${row.depth}`)
                        ? null
                        : row.id || `${row.ordinal}-${row.depth}`,
                    )
                  }
                />
              ))
            ) : (
              <li className="py-4 text-center text-[11.5px] text-muted">
                No span in this run matched that filter.
              </li>
            )}
          </ol>
        </div>
      ) : (
        <ol className="mt-2 border-t border-rule pt-1">
          {bars.length ? (
            bars.map((row) => (
              <li
                key={row.id || `${row.ordinal}-${row.depth}`}
                className="flex items-start justify-between gap-3 py-1.5"
                style={{ paddingLeft: `${Math.min(row.depth, 6) * 13}px` }}
              >
                <span className="min-w-0">
                  <span className={`text-[11.5px] ${row.context ? 'text-muted' : 'text-ink'}`}>
                    {spanLabel(row)}
                  </span>
                  <span className="ml-1.5 font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
                    {row.stage}
                  </span>
                  <span className="mt-0.5 block truncate text-[10.5px] text-muted">
                    {spanDetail(row)}
                  </span>
                </span>
                <span className="shrink-0 text-right font-mono text-[10.5px] tabular-nums text-muted">
                  {formatDuration(row.latency_ms)}
                </span>
              </li>
            ))
          ) : (
            <li className="py-4 text-center text-[11.5px] text-muted">
              No span in this run matched that filter.
            </li>
          )}
        </ol>
      )}

      {summary.stages?.length > 1 ? (
        <details className="mt-3">
          <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
            Spend by stage
          </summary>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[420px] text-[11px]">
              <caption className="sr-only">
                Tokens, cost and errors per stage of this run
              </caption>
              <thead>
                <tr className="border-b border-rule text-[10px] uppercase tracking-[0.1em] text-muted">
                  <th scope="col" className="pb-1 pr-2 text-left font-normal">
                    Stage
                  </th>
                  <th scope="col" className="pb-1 pr-2 text-right font-normal">
                    Spans
                  </th>
                  <th scope="col" className="pb-1 pr-2 text-right font-normal">
                    Tokens
                  </th>
                  <th scope="col" className="pb-1 pr-2 text-right font-normal">
                    Billed
                  </th>
                  <th scope="col" className="pb-1 pr-2 text-right font-normal">
                    Est.
                  </th>
                  <th scope="col" className="pb-1 text-right font-normal">
                    Err
                  </th>
                </tr>
              </thead>
              <tbody>
                {summary.stages.map((bucket) => (
                  <StageRow key={bucket.stage} bucket={bucket} />
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}

      <p className="mt-2 text-[10.5px] leading-relaxed text-muted">
        Bars sit on the run&apos;s own clock, so overlap and waiting are visible; a span with no
        recorded duration stays a hairline rather than being widened to look like work. Every span
        names the audit event that reports it, or records that it has none. Billed and estimated
        cost are shown apart because they are not the same number.
      </p>
    </section>
  );
}
