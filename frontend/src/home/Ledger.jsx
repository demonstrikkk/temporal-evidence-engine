import { Fragment, useState } from 'react';
import { LEDGER, VERDICTS } from './demo.js';

const TONE = {
  ok: { pill: 'text-ok border-ok/40 bg-ok-soft', dot: 'bg-ok' },
  info: { pill: 'text-info border-info/40 bg-info-soft', dot: 'bg-info' },
  danger: { pill: 'text-danger border-danger/40 bg-danger-soft', dot: 'bg-danger' },
  muted: { pill: 'text-muted border-rule-2 bg-warm/60', dot: 'bg-muted' },
};

function Pill({ status }) {
  const meta = VERDICTS[status];
  const tone = TONE[meta.tone];
  return (
    <span className={`ledger-pill ${tone.pill}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} aria-hidden="true" />
      {meta.label}
    </span>
  );
}

/**
 * The ledger.
 *
 * Rows are deliberately chosen to be near-identical: four of these claims are
 * the same sentence about the same product. The only thing that separates them
 * is a date and a reason code — which is exactly the distinction a reader
 * cannot make from the answer alone. Rows open in place so the quote stays
 * attached to the verdict it produced.
 */
export default function Ledger() {
  const [open, setOpen] = useState(0);

  return (
    <div className="ledger-wrap">
      <table className="ledger">
        <caption className="sr-only">
          Example claims with the verdict, reason code, event date and source that produced it.
        </caption>
        <thead>
          <tr>
            <th scope="col">claim</th>
            <th scope="col">verdict</th>
            <th scope="col">event date</th>
            <th scope="col" className="col-source">source</th>
          </tr>
        </thead>
        <tbody>
          {LEDGER.map((row, index) => {
            const isOpen = open === index;
            const meta = VERDICTS[row.status];
            return (
              <Fragment key={`${row.claim}-${row.reason}-${index}`}>
                <tr className={`ledger-row ${isOpen ? 'is-open' : ''}`}>
                  <td className="ledger-claim-cell">
                    <button
                      type="button"
                      className="ledger-claim"
                      aria-expanded={isOpen}
                      aria-controls={`ledger-detail-${index}`}
                      onClick={() => setOpen(isOpen ? -1 : index)}
                    >
                      <span className="ledger-caret" aria-hidden="true">
                        {isOpen ? '−' : '+'}
                      </span>
                      <span>{row.claim}</span>
                    </button>
                  </td>
                  <td>
                    <Pill status={row.status} />
                    <code className="ledger-reason">{row.reason}</code>
                  </td>
                  <td className="ledger-date">{row.event || 'missing'}</td>
                  <td className="ledger-source col-source">{row.source}</td>
                </tr>
                <tr id={`ledger-detail-${index}`} hidden={!isOpen} className="ledger-detail">
                  <td colSpan={4}>
                    <div className="ledger-quote">
                      <span className="quote-key">{meta.label} · generated {row.generated}</span>
                      <blockquote>{row.quote}</blockquote>
                      <p className="ledger-detail-source">{row.source}</p>
                    </div>
                  </td>
                </tr>
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
