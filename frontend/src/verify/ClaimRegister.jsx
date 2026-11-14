/**
 * The claim register: every extracted claim, one row each, never filtered by
 * verdict.
 *
 * A verifier that hides the unverifiable claims is reporting a success rate,
 * not a verification. The bar at the top is the summary; the rows are the run.
 */
import { freshnessTone, freshnessWord, reasonLabel, statusMeta } from './model.js';
import { StatusPill } from './ui.jsx';

const ORDER = ['supported', 'changed', 'contradicted', 'unverifiable'];

/* Fill classes, not inline colours: the verdict palette lives in the Tailwind
   theme so `changed` can never drift back to green. */
const FILL = {
  supported: 'bg-ok',
  changed: 'bg-info',
  contradicted: 'bg-danger',
  unverifiable: 'bg-muted',
  pending: 'bg-rule-2',
};

/**
 * The distribution, as a bar before it is a legend.
 *
 * Undecided claims are simply absent from the bar, which leaves the warm track
 * showing through — the visual length is the settled share, and the legend says
 * how many rows are still pending. It is `role="img"` with a sentence, because
 * a series of coloured divs is not readable.
 */
export function VerdictBar({ counts }) {
  const total = counts?.total || 0;
  const pending = counts?.pending || 0;

  if (!total) {
    return (
      <div className="verdict-bar" role="img" aria-label="No claims extracted yet">
        <span className={FILL.pending} style={{ width: '100%' }} />
      </div>
    );
  }

  const label = [
    ...ORDER.map((status) => `${counts?.[status] || 0} ${statusMeta(status).label.toLowerCase()}`),
    ...(pending ? [`${pending} pending`] : []),
    `of ${total}`,
  ].join(', ');

  return (
    <div>
      <div className="verdict-bar" role="img" aria-label={label}>
        {ORDER.map((status) => {
          const count = counts?.[status] || 0;
          if (!count) return null;
          return (
            <span
              key={status}
              className={FILL[status]}
              style={{ width: `${(count / total) * 100}%` }}
              title={`${statusMeta(status).label}: ${count}`}
            />
          );
        })}
      </div>
      <ul className="verdict-legend">
        {ORDER.map((status) => {
          const count = counts?.[status] || 0;
          const meta = statusMeta(status);
          return (
            <li key={status} title={meta.note} className={count ? '' : 'opacity-60'}>
              <span className={`chip ${count ? FILL[status] : FILL.pending}`} aria-hidden="true" />
              {meta.label}
              <b>{count}</b>
            </li>
          );
        })}
        {pending ? (
          <li title="Extracted, not settled yet">
            <span className={`chip ${FILL.pending}`} aria-hidden="true" />
            Pending
            <b>{pending}</b>
          </li>
        ) : null}
      </ul>
    </div>
  );
}

export default function ClaimRegister({ claims, selectedId, onSelect }) {
  if (!claims?.length) {
    return (
      <p className="px-1 py-6 text-center text-[12px] text-muted">
        No claims have been extracted yet.
      </p>
    );
  }

  return (
    <ul className="claim-list" aria-label="Claim register">
      {claims.map((claim) => {
        const status = claim.status || 'pending';
        const meta = statusMeta(status);
        const selected = claim.id === selectedId;
        return (
          <li key={claim.id}>
            <button
              type="button"
              onClick={() => onSelect?.(claim.id)}
              aria-current={selected ? 'true' : undefined}
              aria-label={`Claim ${(claim.ordinal ?? 0) + 1}: ${meta.label}`}
              className="claim-row"
            >
              <span className={`claim-rail ${FILL[status] || FILL.pending}`} aria-hidden="true" />
              <span className="claim-ordinal">{String((claim.ordinal ?? 0) + 1).padStart(2, '0')}</span>
              <span className="min-w-0">
                <span className="claim-text">{claim.claim_text}</span>
                <span className="claim-meta">
                  {claim.object_raw ? <span className="claim-value">{claim.object_raw}</span> : null}
                  <span className="truncate">{reasonLabel(claim.reason_code)}</span>
                  {claim.evidence_count ? <span>· {claim.evidence_count} evidence</span> : null}
                  {claim.volatility_class ? (
                    <span className="claim-flag">{claim.volatility_class}</span>
                  ) : null}
                  {claim.freshness_index !== null && claim.freshness_index !== undefined ? (
                    <span
                      className="claim-fresh"
                      data-tone={freshnessTone(claim.freshness_index)}
                      title={`Freshness ${claim.freshness_index}/100 — the age of the last verification, not a probability that the claim is true (${freshnessWord(
                        claim.freshness_index
                      )}).`}
                    >
                      fresh {claim.freshness_index}
                    </span>
                  ) : null}
                </span>
              </span>
              <span className="claim-status">
                <StatusPill status={status} size="sm" />
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
