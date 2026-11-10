/**
 * The inspector: everything the verdict rests on, for one claim.
 *
 * The order is the argument. Verdict first, then the replacement, then the
 * quotes, then the four dates. A reviewer should be able to decide whether to
 * trust the verdict without scrolling, and audit it without leaving the pane.
 */
import { useState } from 'react';
import { useClaim } from './useVerifyRun';
import { reasonLabel, shortDate, shortUrl, statusMeta, timeOnly } from './model.js';
import {
  CopyButton,
  DateRow,
  Empty,
  SectionHeading,
  SkeletonBlock,
  StatusPill,
  TabPanel,
  Tabs,
} from './ui.jsx';

function eventStatusLabel(status) {
  if (status === 'explicit') return 'stated in source';
  if (status === 'relative') return 'anchored to publication';
  return 'not stated in source';
}

function SettlementBadge({ status, reason }) {
  // `accepted`/`rejected` is the stored vocabulary: did the settlement gate
  // accept this proposal? The badge says it in words a reader recognises, but
  // the state comes from the record rather than from a second guess.
  const settled = status === 'accepted';
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${
        settled
          ? 'border-ok/30 bg-ok-soft text-ok'
          : 'border-warn/30 bg-warn-soft text-warn'
      }`}
      title={reason || (settled ? 'the quote establishes the asserted value' : '')}
    >
      {settled ? 'settled' : 'not settled'}
    </span>
  );
}

function ValueChange({ claim }) {
  const diff = claim.value_diff;
  if (!diff || !Object.keys(diff).length) return null;
  const from = claim.object_raw;
  const to = claim.evidence?.find((row) => row.candidate_value_raw)?.candidate_value_raw;
  return (
    <section className="surface-inset p-3">
      <p className="eyebrow">Replacement</p>
      <div className="mt-1.5 flex flex-wrap items-baseline gap-2 font-mono text-[13px]">
        <span className="text-muted line-through decoration-danger/50">{from || '—'}</span>
        <span className="text-muted" aria-hidden="true">
          →
        </span>
        <span className="font-medium text-info">{to || '—'}</span>
      </div>
      <dl className="mt-2 space-y-1">
        {Object.entries(diff).map(([field, change]) => (
          <div key={field} className="flex items-baseline gap-2 text-[11px]">
            <dt className="w-24 shrink-0 text-muted">{field}</dt>
            <dd className="font-mono text-ink-2">
              {String(change.from ?? '—')} → {String(change.to ?? '—')}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function EvidenceCard({ item, index, answerGeneratedAt }) {
  const publication = shortDate(item.published_date);
  const event = shortDate(item.event_date);
  const missingEvent = !item.event_date;
  return (
    <article className="rounded-sm border border-rule bg-paper-3/60">
      <header className="flex items-start justify-between gap-2 border-b border-rule/70 px-3 py-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-[10.5px] uppercase tracking-[0.1em] text-muted">
            <span className="font-mono">E{index + 1}</span>
            <span aria-hidden="true">·</span>
            <span className="truncate">{item.publisher || shortUrl(item.source_url, 30)}</span>
          </p>
          <a
            className="mt-0.5 block truncate text-[12px] text-ink hover:text-accent"
            href={item.source_url}
            target="_blank"
            rel="noreferrer noopener"
            title={item.source_title || item.source_url}
          >
            {item.source_title || shortUrl(item.source_url)}
          </a>
        </div>
        <SettlementBadge status={item.settlement_status} reason={item.settlement_reason} />
      </header>

      <div className="px-3 py-2.5">
        {item.quote_text ? (
          <blockquote className="border-l-2 border-accent/40 pl-2.5">
            <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-ink-2">
              <mark className="proof">{item.quote_text}</mark>
            </p>
          </blockquote>
        ) : (
          <p className="text-[11.5px] text-muted">No quote was recorded for this source.</p>
        )}

        {item.settlement_reason ? (
          <p className="mt-2 text-[11px] leading-snug text-warn">
            <span className="font-medium">Settlement:</span> {item.settlement_reason}
          </p>
        ) : null}

        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-muted">
          <span>
            Proposed: <span className="font-mono text-ink-2">{item.relationship_proposed || '—'}</span>
          </span>
          {item.candidate_value_raw ? (
            <span>
              Replacement:{' '}
              <span className="font-mono text-info">{item.candidate_value_raw}</span>
            </span>
          ) : null}
          {item.extraction_method ? <span>{item.extraction_method.replaceAll('_', ' ')}</span> : null}
        </div>
      </div>

      <div className="border-t border-rule/70 px-3 py-2">
        <DateRow
          label="Event date"
          value={missingEvent ? 'missing' : event}
          tone={missingEvent ? 'text-warn' : 'text-ink-2'}
          title={
            item.event_date_quote
              ? `“${item.event_date_quote}” — ${eventStatusLabel(item.event_date_status)}`
              : eventStatusLabel(item.event_date_status)
          }
        />
        <DateRow
          label="Published"
          value={publication}
          title={item.published_date_source ? `source: ${item.published_date_source}` : undefined}
        />
        <DateRow
          label="Retrieved"
          value={item.retrieved_at ? `${shortDate(item.retrieved_at)} ${timeOnly(item.retrieved_at)}` : '—'}
        />
        <DateRow
          label="Answer generated"
          value={answerGeneratedAt ? `${shortDate(answerGeneratedAt)} ${timeOnly(answerGeneratedAt)}` : 'unknown'}
          tone={answerGeneratedAt ? '' : 'text-warn'}
        />
      </div>
    </article>
  );
}

export default function ClaimInspector({ runId, claimId, answerGeneratedAt, version }) {
  const { claim, error, loading } = useClaim(runId, claimId, version);
  const [tab, setTab] = useState('evidence');

  if (!claimId) {
    return (
      <div className="surface h-full">
        <Empty title="Select a claim">
          Choose a marked span in the answer, or a row in the register, to see what the verdict
          rests on.
        </Empty>
      </div>
    );
  }

  if (loading && !claim) {
    return (
      <div className="surface space-y-4 p-4">
        <SkeletonBlock lines={2} />
        <div className="skeleton h-24" />
        <SkeletonBlock lines={5} />
      </div>
    );
  }

  if (error && !claim) {
    return (
      <div className="surface p-4" role="alert">
        <p className="text-[13px] text-danger">Could not load this claim.</p>
        <p className="mt-1 text-[11.5px] text-muted">{error.message || String(error)}</p>
      </div>
    );
  }

  if (!claim) return null;

  const meta = statusMeta(claim.status);
  const evidence = claim.evidence || [];

  return (
    <div className="surface overflow-hidden" aria-live="polite">
      <div className="border-b border-rule px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill status={claim.status} />
          <span className="font-mono text-[10.5px] text-muted">{claim.reason_code || 'pending'}</span>
          {claim.rule ? (
            <span className="ml-auto rounded-xs border border-rule-2 bg-warm/60 px-1.5 py-0.5 font-mono text-[10px] text-ink-2">
              {claim.rule}
            </span>
          ) : null}
        </div>
        <p className="mt-2 font-display text-[17px] leading-snug text-ink">{claim.claim_text}</p>
        <p className="mt-2 text-[12px] leading-relaxed text-ink-2">
          {claim.explanation || meta.note}
        </p>
        <p className="mt-1.5 text-[10.5px] text-muted">{meta.note}</p>
      </div>

      <div className="space-y-4 px-4 py-3">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
          {claim.subject ? (
            <span>
              subject <span className="text-ink-2">{claim.subject}</span>
            </span>
          ) : null}
          {claim.predicate ? (
            <span>
              predicate <span className="text-ink-2">{claim.predicate}</span>
            </span>
          ) : null}
          {claim.object_raw ? (
            <span>
              value <span className="font-mono text-ink-2">{claim.object_raw}</span>
            </span>
          ) : null}
        </div>

        {claim.status === 'changed' ? <ValueChange claim={claim} /> : null}

        <div>
          <Tabs
            label="Claim detail"
            value={tab}
            onChange={setTab}
            tabs={[
              { id: 'evidence', label: 'Evidence', count: evidence.length },
              { id: 'history', label: 'History', count: (claim.timeline || []).length },
            ]}
          />
          <div className="pt-3">
            <TabPanel id="evidence" active={tab === 'evidence'}>
              {evidence.length ? (
                <div className="space-y-3">
                  {evidence.map((item, index) => (
                    <EvidenceCard
                      key={item.id || index}
                      item={item}
                      index={index}
                      answerGeneratedAt={answerGeneratedAt}
                    />
                  ))}
                </div>
              ) : (
                <Empty title="Nothing was attached">
                  {claim.reason_code === 'no_source'
                    ? 'No source with a locatable quote was found. That is an abstention, not a refutation.'
                    : 'No evidence row exists for this claim. The verdict explains why.'}
                </Empty>
              )}
            </TabPanel>

            <TabPanel id="history" active={tab === 'history'}>
              <ol className="space-y-2">
                {(claim.timeline || []).map((row, index) => (
                  <li
                    key={index}
                    className="flex items-baseline justify-between gap-3 border-b border-rule/60 pb-2 text-[11.5px] last:border-0"
                  >
                    <span className="min-w-0">
                      <span className="text-ink-2">
                        {row.previous_status || 'pending'} → {row.status}
                      </span>
                      <span className="ml-2 text-[10.5px] text-muted">
                        {reasonLabel(row.reason_code)}
                      </span>
                    </span>
                    <span className="shrink-0 font-mono text-[10.5px] text-muted">
                      {shortDate(row.decided_at)} {timeOnly(row.decided_at)}
                    </span>
                  </li>
                ))}
                {!(claim.timeline || []).length ? (
                  <li className="text-[11.5px] text-muted">No status transitions recorded.</li>
                ) : null}
              </ol>

              {(claim.assertions || []).length ? (
                <div className="mt-4">
                  <SectionHeading>Value over time</SectionHeading>
                  <ul className="mt-2 space-y-1.5">
                    {claim.assertions.map((row) => (
                      <li
                        key={row.id}
                        className="flex items-baseline justify-between gap-3 text-[11.5px]"
                      >
                        <span className="min-w-0">
                          <span className="font-mono text-ink-2">{row.value_raw || '—'}</span>
                          <span className="ml-2 text-[10.5px] text-muted">
                            {row.is_original ? 'original' : 'replacement'}
                          </span>
                        </span>
                        <span className="shrink-0 font-mono text-[10.5px] text-muted">
                          from {shortDate(row.valid_from)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </TabPanel>
          </div>
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-rule pt-3">
          <span className="text-[10.5px] text-muted">
            {claim.next_check_at ? `next recommended check ${shortDate(claim.next_check_at)}` : 'no recheck scheduled'}
          </span>
          <CopyButton text={claim.claim_text} label="Copy claim" />
        </div>
      </div>
    </div>
  );
}
