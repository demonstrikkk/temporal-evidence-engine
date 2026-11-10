/**
 * The claim register: every extracted claim, one row each, never filtered by
 * verdict.
 *
 * A verifier that hides the unverifiable claims is reporting a success rate,
 * not a verification. The counts at the top are the summary; the rows are the
 * run.
 */
import { reasonLabel, statusMeta } from './model.js';
import { StatusPill } from './ui.jsx';

export default function ClaimRegister({ claims, selectedId, onSelect }) {
  if (!claims?.length) {
    return (
      <p className="px-1 py-6 text-center text-[12px] text-muted">
        No claims have been extracted yet.
      </p>
    );
  }

  return (
    <ul className="divide-y divide-rule/70" aria-label="Claim register">
      {claims.map((claim) => {
        const status = claim.status || 'pending';
        const selected = claim.id === selectedId;
        return (
          <li key={claim.id}>
            <button
              type="button"
              onClick={() => onSelect?.(claim.id)}
              data-active={selected}
              aria-current={selected ? 'true' : undefined}
              className="row grid w-full grid-cols-[26px_minmax(0,1fr)_auto] items-start gap-3 px-2 py-2.5 text-left focus:outline-none focus-visible:ring-4 focus-visible:ring-[var(--accent-ring)] sm:grid-cols-[26px_minmax(0,1fr)_88px_auto]"
            >
              <span className="pt-0.5 font-mono text-[10.5px] tabular-nums text-muted">
                {String((claim.ordinal ?? 0) + 1).padStart(2, '0')}
              </span>
              <span className="min-w-0">
                <span className="block text-[12.5px] leading-snug text-ink">
                  {claim.claim_text}
                </span>
                <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10.5px] text-muted">
                  {claim.object_raw ? (
                    <span className="font-mono text-ink-2">{claim.object_raw}</span>
                  ) : null}
                  <span className="truncate">{reasonLabel(claim.reason_code)}</span>
                  {claim.evidence_count ? <span>· {claim.evidence_count} evidence</span> : null}
                </span>
              </span>
              <span className="hidden pt-0.5 text-[10.5px] text-muted sm:block">
                {claim.volatility_class || ''}
              </span>
              <span className="pt-0.5">
                <StatusPill status={status} size="sm" />
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function StatusSummary({ counts }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5" aria-label="Verdict counts">
      {['supported', 'changed', 'contradicted', 'unverifiable'].map((status) => {
        const count = counts?.[status] || 0;
        const meta = statusMeta(status);
        return (
          <span
            key={status}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10.5px] font-medium ${
              count ? meta.cls : 'border-rule bg-paper-2 text-muted/70'
            }`}
            title={meta.note}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${count ? meta.dot : 'bg-rule-2'}`} aria-hidden="true" />
            {meta.label}
            <span className="tabular-nums">{count}</span>
          </span>
        );
      })}
    </div>
  );
}
