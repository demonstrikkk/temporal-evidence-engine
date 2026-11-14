/**
 * What the research stage fetched, and what it failed to fetch.
 *
 * Tavily is half of the mandatory stack, and "we use Tavily" is a claim like any
 * other: this panel answers it with the run's own `source.*` events rather than
 * with a logo. A failed extraction is counted, never skipped — the engine
 * abstains on a claim it could not read, and that abstention is the honest
 * outcome, not a hidden one.
 *
 * The panel renders nothing until the run has a research event, so a replayed
 * snapshot does not show invented zeros.
 */
import { researchSummary, shortDate, shortUrl, timeOnly } from './model.js';
import { SectionHeading } from './ui.jsx';

export default function ResearchLedger({ events }) {
  const summary = researchSummary(events);
  // A plan with zero queries is a fact worth printing — offline mode asks none,
  // and a panel that vanished there would hide the reason.
  if (!summary.plans && !summary.stored && !summary.failed && !summary.reused) return null;

  // A run recorded before the plan event existed fetched pages without ever
  // saying how many queries it asked. That is unrecorded, not zero, and a zero
  // beside 28 stored pages reads as a contradiction.
  const queriesUnknown = summary.plans === 0 && summary.stored > 0;

  return (
    <section className="surface px-4 py-3.5" aria-label="Sources fetched">
      <SectionHeading
        aside={
          <span>
            {summary.stored} stored
            {summary.failed ? (
              <span className="text-warn"> · {summary.failed} failed</span>
            ) : null}
          </span>
        }
      >
        Sources · Tavily
      </SectionHeading>

      <dl className="strip-stats mt-3">
        <div>
          <dt>queries asked</dt>
          <dd className={queriesUnknown ? 'text-muted' : ''}>
            {queriesUnknown ? '—' : summary.planned}
          </dd>
        </div>
        <div>
          <dt>pages stored</dt>
          <dd>{summary.stored}</dd>
        </div>
        <div>
          <dt>sources reused</dt>
          <dd>{summary.reused}</dd>
        </div>
        <div>
          <dt>extractions failed</dt>
          <dd className={summary.failed ? 'text-warn' : ''}>{summary.failed}</dd>
        </div>
      </dl>

      {queriesUnknown ? (
        <p className="mt-2 text-[10.5px] leading-snug text-muted">
          This run recorded no research-plan event, so the number of queries it asked is unknown
          rather than zero. The stored pages below are what it actually fetched.
        </p>
      ) : null}

      {summary.sources.length ? (
        <ul className="mt-2">
          {summary.sources.slice(0, 6).map((source) => (
            <li key={source.url + source.chars} className="line-row">
              <span className="min-w-0">
                <span className="block truncate font-mono text-[11px] text-ink-2" title={source.url}>
                  {shortUrl(source.url, 48)}
                </span>
                <span className="block font-mono text-[9.5px] text-muted">
                  {source.chars ? `${source.chars} chars` : 'length unrecorded'}
                  {source.publishedAt ? ` · published ${shortDate(source.publishedAt)}` : ' · publication date unknown'}
                </span>
              </span>
              <span className="shrink-0 font-mono text-[9.5px] text-muted">
                {source.storedAt ? `stored ${timeOnly(source.storedAt)}` : 'stored'}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {summary.budgetStops ? (
        <p className="mt-2 text-[10.5px] leading-snug text-warn">
          The budget mode refused {summary.budgetStops} query plan
          {summary.budgetStops === 1 ? '' : 's'}. Refused questions are reported, not hidden.
        </p>
      ) : null}

      <p className="mt-2.5 border-t border-rule pt-2 text-[10px] leading-relaxed text-muted">
        Discovery and extraction come from Tavily; every page is stored as an append-only revision
        before a quote may cite it, so the offsets in a verdict always index text the store still
        holds.
      </p>
    </section>
  );
}
