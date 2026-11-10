/**
 * The watchlist is a recommendation, and it says so.
 *
 * Claims get a `next_check_at` from their volatility class. Nothing here is
 * scheduled: the product refuses to imply a background job that does not exist.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import { ago, reasonLabel, shortDate } from './model.js';
import { Empty, SectionHeading, SkeletonBlock, StatusPill } from './ui.jsx';

function bucket(nextCheckAt) {
  if (!nextCheckAt) return 'unscheduled';
  const when = new Date(nextCheckAt).getTime();
  if (Number.isNaN(when)) return 'unscheduled';
  if (when <= Date.now()) return 'due';
  if (when <= Date.now() + 7 * 86_400_000) return 'soon';
  return 'scheduled';
}

const GROUPS = [
  {
    id: 'due',
    label: 'Due now',
    hint: 'The recommended recheck time has passed.',
    tone: 'warn',
  },
  {
    id: 'soon',
    label: 'Due within a week',
    hint: 'High-volatility claims come round quickly.',
    tone: 'muted',
  },
  {
    id: 'scheduled',
    label: 'Later',
    hint: 'Stable facts are rechecked less often.',
    tone: 'muted',
  },
  {
    id: 'unscheduled',
    label: 'No recheck scheduled',
    hint: 'The claim has no volatility class, so no interval was assigned.',
    tone: 'muted',
  },
];

function Row({ claim }) {
  return (
    <li>
      <Link
        to={`/runs/${claim.run_id}${claim.id ? `?claim=${claim.id}` : ''}`}
        className="row grid gap-2 px-4 py-2.5 sm:grid-cols-[minmax(0,1fr)_132px_140px] sm:items-center"
      >
        <span className="min-w-0">
          <span className="block text-[12.5px] leading-snug text-ink">{claim.claim_text}</span>
          <span className="mt-0.5 block text-[10.5px] text-muted">
            {reasonLabel(claim.reason_code)}
            {claim.volatility_class ? ` · ${claim.volatility_class}` : ''}
          </span>
        </span>
        <span>
          <StatusPill status={claim.status} size="sm" />
        </span>
        <span className="text-[10.5px] text-muted">
          {claim.next_check_at ? (
            <>
              check {shortDate(claim.next_check_at)}
              <span className="ml-1.5 opacity-80">{ago(claim.next_check_at)}</span>
            </>
          ) : (
            'not scheduled'
          )}
        </span>
      </Link>
    </li>
  );
}

export default function Watchlist() {
  const [rows, setRows] = useState([]);
  const [note, setNote] = useState('');
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api.verify
      .watchlist()
      .then((data) => {
        if (!alive) return;
        setRows(data.claims || []);
        setNote(data.note || '');
        setError(null);
      })
      .catch((err) => alive && setError(err))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const grouped = useMemo(() => {
    const out = { due: [], soon: [], scheduled: [], unscheduled: [] };
    for (const row of rows) out[bucket(row.next_check_at)].push(row);
    return out;
  }, [rows]);

  return (
    <div className="space-y-5 animate-fade-up">
      <div>
        <p className="eyebrow">Recheck</p>
        <h1 className="mt-1 font-display text-[28px] text-ink">Watchlist</h1>
        <p className="mt-1 max-w-2xl text-[12.5px] leading-relaxed text-muted">
          {note || 'Recommendations only. This product does not run a scheduler.'} A claim’s interval
          comes from its volatility class — a price moves faster than a founding date.
        </p>
      </div>

      {loading && !rows.length ? (
        <div className="surface p-4">
          <SkeletonBlock lines={5} />
        </div>
      ) : error && !rows.length ? (
        <div className="surface p-6" role="alert">
          <p className="text-[13px] text-danger">Could not load the watchlist.</p>
          <p className="mt-1 text-[11.5px] text-muted">{error.message || String(error)}</p>
        </div>
      ) : !rows.length ? (
        <div className="surface">
          <Empty title="Nothing to recheck yet">
            Run a verification and the claims will appear here, ordered by when they are worth
            checking again.
          </Empty>
        </div>
      ) : (
        <div className="space-y-5">
          {GROUPS.map((group) => {
            const items = grouped[group.id];
            if (!items.length) return null;
            return (
              <section key={group.id} className="surface overflow-hidden">
                <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-rule px-4 py-2.5">
                  <SectionHeading aside={`${items.length} claim${items.length === 1 ? '' : 's'}`}>
                    {group.label}
                  </SectionHeading>
                  <p className="text-[10.5px] text-muted">{group.hint}</p>
                </div>
                <ul className="divide-y divide-rule/70">
                  {items.map((claim) => (
                    <Row key={`${claim.run_id}-${claim.id}`} claim={claim} />
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
