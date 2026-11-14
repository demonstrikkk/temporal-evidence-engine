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
import { Empty, SkeletonBlock, StatusPill } from './ui.jsx';

const RAIL = {
  supported: 'bg-ok',
  changed: 'bg-info',
  contradicted: 'bg-danger',
  unverifiable: 'bg-muted',
  pending: 'bg-rule-2',
};

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
  },
  {
    id: 'soon',
    label: 'Due within a week',
    hint: 'High-volatility claims come round quickly.',
  },
  {
    id: 'scheduled',
    label: 'Later',
    hint: 'Stable facts are rechecked less often.',
  },
  {
    id: 'unscheduled',
    label: 'No recheck scheduled',
    hint: 'The claim has no volatility class, so no interval was assigned.',
  },
];

function Row({ claim }) {
  return (
    <li>
      <Link
        to={`/runs/${claim.run_id}${claim.id ? `?claim=${claim.id}` : ''}`}
        className="watch-row"
      >
        <span
          className={`watch-rail ${RAIL[claim.status] || RAIL.pending}`}
          aria-hidden="true"
        />
        <span className="min-w-0">
          <span className="watch-text">{claim.claim_text}</span>
          <span className="watch-meta">
            <span className="truncate">{reasonLabel(claim.reason_code)}</span>
            {claim.volatility_class ? <span>· {claim.volatility_class}</span> : null}
            {claim.run_id ? <span>· run {String(claim.run_id).slice(0, 8)}</span> : null}
          </span>
        </span>
        <span className="watch-status">
          <StatusPill status={claim.status} size="sm" />
        </span>
        <span className="watch-when">
          {claim.next_check_at ? (
            <>
              <span>check {shortDate(claim.next_check_at)}</span>
              <em>{ago(claim.next_check_at)}</em>
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
    <div className="animate-fade-up space-y-6">
      <header className="page-head">
        <div>
          <p className="eyebrow">Recheck</p>
          <h1 className="page-title">Watchlist</h1>
          <p className="page-lede">
            {note || 'Recommendations only. This product does not run a scheduler.'} A claim’s
            interval comes from its volatility class — a price moves faster than a founding date.
          </p>
        </div>
        <p className="case-crumb hidden lg:flex">
          <span>{rows.length}</span>
          <span aria-hidden="true">·</span>
          <span>claim{rows.length === 1 ? '' : 's'} on watch</span>
        </p>
      </header>

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
        <>
          <dl className="strip-stats" aria-label="Watchlist totals">
            {GROUPS.map((group) => (
              <div key={group.id}>
                <dt>{group.label}</dt>
                <dd>{grouped[group.id].length}</dd>
              </div>
            ))}
          </dl>

          <div className="space-y-6">
            {GROUPS.map((group) => {
              const items = grouped[group.id];
              if (!items.length) return null;
              return (
                <section key={group.id} className="watch-group" aria-label={group.label}>
                  <div className="watch-group-head">
                    <div>
                      <h2 className="watch-group-title">{group.label}</h2>
                      <p className="watch-hint mt-1">{group.hint}</p>
                    </div>
                    <span className="watch-count">
                      {items.length} claim{items.length === 1 ? '' : 's'}
                    </span>
                  </div>
                  <ul>
                    {items.map((claim) => (
                      <Row key={`${claim.run_id}-${claim.id}`} claim={claim} />
                    ))}
                  </ul>
                </section>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
