/**
 * The run as a tree, with the price attached to each branch.
 *
 * The event rail answers "what happened, in order". This panel answers the two
 * questions the rail cannot: which stage spent the tokens, and what a span
 * actually did. It is a projection of the same rows — every span names the
 * audit event that reports it — so the two cannot disagree.
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
  formatTokens,
  spanCost,
  spanDetail,
  spanLabel,
  spanTone,
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

function SpanRow({ row }) {
  const tone = spanTone(row, { context: row.context });
  const detail = spanDetail(row);
  const cost = spanCost(row);
  return (
    <li
      className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 py-1.5"
      style={{ paddingLeft: `${Math.min(row.depth, 6) * 13}px` }}
    >
      <span className="min-w-0">
        <span className="flex items-baseline gap-1.5">
          <span
            className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${TONE_DOT[tone]}`}
            aria-hidden="true"
          />
          <span className={`text-[11.5px] ${row.context ? 'text-muted' : 'text-ink'}`}>
            {spanLabel(row)}
          </span>
          <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted">
            {row.stage}
          </span>
        </span>
        {detail ? (
          <span
            className="mt-0.5 block truncate pl-3 text-[10.5px] text-muted"
            title={detail}
          >
            {detail}
          </span>
        ) : null}
      </span>
      <span className="text-right font-mono text-[10.5px] leading-relaxed tabular-nums text-muted">
        <span className="block text-ink-2">
          {cost.priced ? formatCost(cost.value) : '—'}
        </span>
        <span className="block">{formatDuration(row.latency_ms)}</span>
      </span>
    </li>
  );
}

export default function TracePanel({ runId, live }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('all');

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

      <div className="mt-3 grid grid-cols-2 gap-4 border-y border-rule py-3 sm:grid-cols-4">
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

      {summary.stages?.length > 1 ? (
        <div className="mt-3 overflow-x-auto">
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
          {rows.length}
          {filter === 'all' ? '' : ` of ${counts.all?.total ?? rows.length}`} shown
        </span>
      </div>

      <ol className="mt-2 border-t border-rule pt-1">
        {rows.length ? (
          rows.map((row) => <SpanRow key={row.id || `${row.ordinal}-${row.depth}`} row={row} />)
        ) : (
          <li className="py-4 text-center text-[11.5px] text-muted">
            No span in this run matched that filter.
          </li>
        )}
      </ol>

      <p className="mt-2 text-[10.5px] leading-relaxed text-muted">
        Every span names the audit event that reports it, or records that it has
        none, so this tree is the run ledger seen with parents, durations and
        prices attached. Billed and estimated cost are shown apart because they
        are not the same number.
      </p>
    </section>
  );
}
