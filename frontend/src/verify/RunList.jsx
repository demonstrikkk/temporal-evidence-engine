/**
 * Every verification run, newest first.
 *
 * A run list is a history of claims, so the prompt is the label and the verdict
 * counts are the substance. The raw id is available but not the headline.
 *
 * Presented as an archive index: a ruled header, one row per run, a status rail
 * that is readable before the pill is.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import { ago, shortDate } from './model.js';
import { Empty, RunStatePill, SkeletonBlock } from './ui.jsx';

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'live', label: 'Live' },
  { id: 'completed', label: 'Completed' },
  { id: 'partial', label: 'Partial' },
  { id: 'failed', label: 'Failed' },
];

const RAIL = {
  completed: 'bg-ok',
  partial: 'bg-warn',
  failed: 'bg-danger',
  running: 'bg-accent',
  queued: 'bg-rule-2',
};

function matches(run, filter) {
  if (filter === 'all') return true;
  if (filter === 'live') return run.status === 'queued' || run.status === 'running';
  return run.status === filter;
}

export default function RunList() {
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');
  // Failed runs are hidden from the default view. They are not deleted and they
  // are not uncounted — the strip still reports how many the archive holds, and
  // the control beside it puts them back. Hiding is a filter with a label, not
  // a second version of the record.
  const [showFailed, setShowFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.verify
        .runs()
        .then((data) => {
          if (!alive) return;
          setRuns(data.runs || []);
          setError(null);
        })
        .catch((err) => alive && setError(err))
        .finally(() => alive && setLoading(false));
    load();
    const timer = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const failedCount = useMemo(
    () => runs.filter((run) => run.status === 'failed').length,
    [runs],
  );

  const visible = useMemo(
    () =>
      runs.filter(
        (run) =>
          matches(run, filter) && (showFailed || filter === 'failed' || run.status !== 'failed'),
      ),
    [runs, filter, showFailed],
  );

  const stats = useMemo(
    () => [
      { label: 'runs on record', value: runs.length },
      { label: 'completed', value: runs.filter((run) => run.status === 'completed').length },
      { label: 'in flight', value: runs.filter((run) => matches(run, 'live')).length },
      {
        label: 'reported failures',
        value: runs.filter((run) => run.status === 'partial' || run.status === 'failed').length,
      },
    ],
    [runs],
  );

  return (
    <div className="space-y-6 animate-fade-up">
      <header className="page-head">
        <div>
          <p className="eyebrow">History</p>
          <h1 className="page-title">Runs</h1>
          <p className="page-lede">
            Each run keeps its own claims, sources and verdicts. Replayed runs are labelled{' '}
            <span className="font-mono text-ink-2">snapshot</span> and keep their original
            timestamps — a cached result is never dressed up as a live one.
          </p>
        </div>
        <Link className="btn-accent" to="/check">
          New check
          <span aria-hidden="true">→</span>
        </Link>
      </header>

      <dl className="strip-stats" aria-label="Run totals">
        {stats.map((stat) => (
          <div key={stat.label}>
            <dt>{stat.label}</dt>
            <dd>{stat.value}</dd>
          </div>
        ))}
      </dl>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="seg" role="group" aria-label="Filter by status">
          {FILTERS.map((item) => {
            const count = runs.filter(
              (run) =>
                matches(run, item.id) &&
                (showFailed || item.id === 'failed' || run.status !== 'failed'),
            ).length;
            const active = filter === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setFilter(item.id)}
                aria-pressed={active}
              >
                {item.label}
                <span className="count">{count}</span>
              </button>
            );
          })}
        </div>
        <p className="flex items-center gap-2 font-mono text-[9.5px] uppercase tracking-[0.14em] text-muted">
          <span>newest first · refreshes every 5s</span>
          {failedCount ? (
            <>
              <span aria-hidden="true">·</span>
              <button
                type="button"
                onClick={() => setShowFailed((value) => !value)}
                aria-pressed={showFailed}
                className="underline decoration-rule-2 underline-offset-2 transition-colors duration-150 hover:text-ink-2"
                title={
                  showFailed
                    ? 'Hide failed runs from the list again'
                    : 'Failed runs are kept on record; this shows them'
                }
              >
                {showFailed ? 'hide' : 'show'} {failedCount} failed
              </button>
            </>
          ) : null}
        </p>
      </div>

      {loading && !runs.length ? (
        <div className="surface p-4">
          <SkeletonBlock lines={6} />
        </div>
      ) : error && !runs.length ? (
        <div className="surface p-6" role="alert">
          <p className="text-[13px] text-danger">Could not load runs.</p>
          <p className="mt-1 text-[11.5px] text-muted">{error.message || String(error)}</p>
        </div>
      ) : !runs.length ? (
        <div className="surface px-6 py-10 text-center">
          <p className="font-display text-[20px] text-ink">Nothing on record yet</p>
          <p className="mx-auto mt-1.5 max-w-sm text-[12.5px] leading-relaxed text-muted">
            Start with a pasted answer and the time it was generated. Every stage of the run is
            recorded, including the ones that fail.
          </p>
          <Link className="btn-accent mt-4" to="/check">
            Verify an answer
          </Link>
        </div>
      ) : !visible.length ? (
        <div className="surface">
          <Empty title="Nothing matches that filter">
            Switch the filter to see the rest of the history.
          </Empty>
        </div>
      ) : (
        <section aria-label="Run archive">
          <div className="archive">
            <div className="archive-head" aria-hidden="true">
              <span />
              <span>Answer</span>
              <span>Status</span>
              <span className="archive-hide">Started</span>
              <span className="archive-hide">Rule</span>
              <span />
            </div>
            <ul>
              {visible.map((run) => (
                <li key={run.id}>
                  <Link to={`/runs/${run.id}`} className="archive-row">
                    <span
                      className={`archive-rail ${RAIL[run.status] || 'bg-rule-2'}`}
                      aria-hidden="true"
                    />
                    <span className="min-w-0">
                      <span className="archive-prompt">{run.prompt || 'Untitled answer'}</span>
                      <span className="archive-sub">
                        {String(run.id).slice(0, 8)}
                        {run.generated_at
                          ? ` · answer generated ${shortDate(run.generated_at)}`
                          : ' · generation time unknown'}
                        {run.counters?.claims
                          ? ` · ${run.counters.claims} claim${run.counters.claims === 1 ? '' : 's'}`
                          : ''}
                      </span>
                    </span>
                    <span className="archive-status">
                      <RunStatePill status={run.status} />
                    </span>
                    <span className="archive-cell archive-hide">
                      {shortDate(run.started_at)}
                      <span className="ml-1.5 opacity-80">· {ago(run.started_at)}</span>
                    </span>
                    <span className="archive-cell archive-hide">{run.rule_version || '—'}</span>
                    <span className="archive-arrow" aria-hidden="true">
                      →
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}
    </div>
  );
}
