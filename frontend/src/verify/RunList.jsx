/**
 * Every verification run, newest first.
 *
 * A run list is a history of claims, so the prompt is the label and the verdict
 * counts are the substance. The raw id is available but not the headline.
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

  const visible = useMemo(() => runs.filter((run) => matches(run, filter)), [runs, filter]);

  return (
    <div className="space-y-5 animate-fade-up">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow">History</p>
          <h1 className="mt-1 font-display text-[28px] text-ink">Runs</h1>
          <p className="mt-1 text-[12.5px] text-muted">
            Each run keeps its own claims, sources and verdicts. Replayed runs are labelled
            <span className="mx-1 font-mono text-ink-2">snapshot</span>and keep their original
            timestamps.
          </p>
        </div>
        <Link className="btn-primary" to="/">
          New check
        </Link>
      </div>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by status">
        {FILTERS.map((item) => {
          const count = runs.filter((run) => matches(run, item.id)).length;
          const active = filter === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => setFilter(item.id)}
              aria-pressed={active}
              className={`btn btn-xs border ${
                active
                  ? 'border-ink bg-ink text-paper-2'
                  : 'border-rule bg-paper-2 text-muted hover:text-ink'
              }`}
            >
              {item.label}
              <span className="tabular-nums opacity-70">{count}</span>
            </button>
          );
        })}
      </div>

      <section className="surface overflow-hidden">
        <div className="hidden grid-cols-[minmax(0,1fr)_120px_170px_120px] gap-3 border-b border-rule px-4 py-2 text-[10px] uppercase tracking-[0.12em] text-muted sm:grid">
          <span>Answer</span>
          <span>Status</span>
          <span>Started</span>
          <span>Rule</span>
        </div>

        {loading && !runs.length ? (
          <div className="p-4">
            <SkeletonBlock lines={5} />
          </div>
        ) : error && !runs.length ? (
          <div className="p-6" role="alert">
            <p className="text-[13px] text-danger">Could not load runs.</p>
            <p className="mt-1 text-[11.5px] text-muted">{error.message || String(error)}</p>
          </div>
        ) : !visible.length ? (
          <Empty title={runs.length ? 'Nothing matches that filter' : 'No runs yet'}>
            {runs.length
              ? 'Switch the filter to see the rest of the history.'
              : 'Start with a pasted answer and the time it was generated.'}
          </Empty>
        ) : (
          <ul className="divide-y divide-rule/70">
            {visible.map((run) => (
              <li key={run.id}>
                <Link
                  to={`/runs/${run.id}`}
                  className="row grid gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_120px_170px_120px] sm:items-center sm:gap-3"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[13px] text-ink">
                      {run.prompt || 'Untitled answer'}
                    </span>
                    <span className="mt-0.5 block font-mono text-[10px] text-muted">
                      {String(run.id).slice(0, 8)}
                      {run.generated_at ? ` · generated ${shortDate(run.generated_at)}` : ''}
                    </span>
                  </span>
                  <span>
                    <RunStatePill status={run.status} />
                  </span>
                  <span className="text-[11.5px] text-muted">
                    {shortDate(run.started_at)}
                    <span className="ml-1.5 text-[10.5px]">· {ago(run.started_at)}</span>
                  </span>
                  <span className="font-mono text-[10.5px] text-muted">
                    {run.rule_version || '—'}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
