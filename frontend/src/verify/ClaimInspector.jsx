/**
 * The inspector: everything the verdict rests on, for one claim.
 *
 * The order is the argument. Verdict first, then the replacement, then the
 * quotes, then the four dates. A reviewer should be able to decide whether to
 * trust the verdict without scrolling, and audit it without leaving the pane.
 */
import { useState } from 'react';
import { useClaim } from './useVerifyRun';
import {
  freshnessTone,
  freshnessWord,
  halfLifeLabel,
  reasonLabel,
  shortDate,
  shortUrl,
  statusMeta,
  timeOnly,
} from './model.js';
import {
  CopyButton,
  Empty,
  FreshnessMeter,
  SectionHeading,
  SkeletonBlock,
  TabPanel,
  Tabs,
} from './ui.jsx';
import ProofGraph from './ProofGraph.jsx';

/* The stamp inherits its colour from the verdict tone; `changed` is amber, not
   green, and the border is drawn by the component so the two never diverge. */
const STAMP = {
  ok: 'border-ok text-ok bg-ok-soft',
  info: 'border-info text-info bg-info-soft',
  danger: 'border-danger text-danger bg-danger-soft',
  muted: 'border-rule-2 text-muted bg-warm/60',
};

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
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.08em] ${
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
    <section className="replacement" aria-label="Replacement value">
      <p className="case-sub">
        Replacement
        <b>
          {Object.keys(diff).length} field{Object.keys(diff).length === 1 ? '' : 's'} moved
        </b>
      </p>
      <div className="replacement-values">
        <span className="replacement-from">{from || '—'}</span>
        <span className="replacement-arrow" aria-hidden="true">
          →
        </span>
        <span className="replacement-to">{to || '—'}</span>
      </div>
      <dl className="replacement-diff">
        {Object.entries(diff).map(([field, change]) => (
          <div key={field}>
            <dt>{field}</dt>
            <dd>
              {String(change.from ?? '—')} → {String(change.to ?? '—')}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function EvidenceCard({ item, index, answerGeneratedAt }) {
  const event = shortDate(item.event_date);
  const missingEvent = !item.event_date;
  const retrieved = item.retrieved_at
    ? `${shortDate(item.retrieved_at)} ${timeOnly(item.retrieved_at)}`
    : '—';
  const generated = answerGeneratedAt
    ? `${shortDate(answerGeneratedAt)} ${timeOnly(answerGeneratedAt)}`
    : 'unknown';

  return (
    <article className="evidence-card">
      <header className="evidence-head">
        <div className="min-w-0">
          <p className="evidence-id">
            evidence {String(index + 1).padStart(2, '0')}
            {item.publisher ? ` · ${item.publisher}` : item.source_url ? ` · ${shortUrl(item.source_url, 26)}` : ''}
          </p>
          <a
            className="evidence-title"
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

      {item.quote_text ? (
        <blockquote className="evidence-quote">
          <mark className="proof">{item.quote_text}</mark>
        </blockquote>
      ) : (
        <p className="evidence-quote missing">No quote was recorded for this source.</p>
      )}

      {item.settlement_reason ? (
        <p className="evidence-meta">
          <span className="text-warn">
            settlement · <b>{item.settlement_reason}</b>
          </span>
        </p>
      ) : null}

      <p className="evidence-meta">
        <span>
          proposed <b>{item.relationship_proposed || '—'}</b>
        </span>
        {item.candidate_value_raw ? (
          <span>
            replacement <b className="text-ember-deep">{item.candidate_value_raw}</b>
          </span>
        ) : null}
        {item.extraction_method ? <span>{item.extraction_method.replaceAll('_', ' ')}</span> : null}
      </p>

      <dl className="date-grid">
        <div>
          <dt>event date</dt>
          <dd
            className={missingEvent ? 'missing' : ''}
            title={
              item.event_date_quote
                ? `“${item.event_date_quote}” — ${eventStatusLabel(item.event_date_status)}`
                : eventStatusLabel(item.event_date_status)
            }
          >
            {missingEvent ? 'missing' : event}
          </dd>
        </div>
        <div>
          <dt>published</dt>
          <dd title={item.published_date_source ? `source: ${item.published_date_source}` : undefined}>
            {shortDate(item.published_date)}
          </dd>
        </div>
        <div>
          <dt>retrieved</dt>
          <dd>{retrieved}</dd>
        </div>
        <div>
          <dt>answer generated</dt>
          <dd className={answerGeneratedAt ? '' : 'missing'}>{generated}</dd>
        </div>
      </dl>
    </article>
  );
}

function Freshness({ claim }) {
  const index = claim.freshness_index;
  const scored = index !== null && index !== undefined;
  const tone = freshnessTone(index);
  const lastVerified = claim.last_verified_at;

  return (
    <section className="freshness" aria-label="Freshness">
      <p className="case-sub">
        Freshness
        <b className={`fresh-word tone-${tone}`}>
          {freshnessWord(index)}
          {scored ? <span className="fresh-scale"> / 100</span> : null}
        </b>
      </p>
      <div className="mt-1.5 flex items-center gap-2.5">
        <FreshnessMeter index={scored ? index : null} />
        <span className="fresh-note">
          half-life {halfLifeLabel(claim.half_life_hours)} · {claim.volatility_class || 'unclassified'}
        </span>
      </div>
      <p className="fresh-note mt-1.5">
        {scored
          ? `last verified ${shortDate(lastVerified)}`
          : 'This claim was never settled against a source, so there is no verification age to score.'}
      </p>
      <p className="fresh-caveat">
        Freshness is the age of the last verification. It is not a probability that the claim is
        true.
      </p>
    </section>
  );
}

function Corroboration({ data }) {
  if (!data) return null;
  const independence = data.independence || {};
  const quality = data.quality || {};
  if (!independence.source_count) return null;

  const syndicated = Number(independence.syndicated_groups || 0) > 0;
  const tiers = quality.tiers || {};
  const tierRows = [
    ['high', 'text-ok border-ok/35 bg-ok-soft'],
    ['medium', 'text-warn border-warn/35 bg-warn-soft'],
    ['low', 'text-muted border-rule-2 bg-warm/60'],
  ].filter(([tier]) => Number(tiers[tier] || 0) > 0);

  return (
    <section aria-label="Source corroboration">
      <p className="case-sub">
        Corroboration
        <b className={syndicated ? 'text-warn' : 'text-ok'}>
          {independence.independent_origins}{' '}
          {independence.independent_origins === 1 ? 'independent origin' : 'independent origins'}
        </b>
      </p>
      <p className="mt-1.5 text-[11px] leading-snug text-ink-2">
        {independence.source_count}{' '}
        {independence.source_count === 1 ? 'source' : 'sources'} · effective corroboration{' '}
        <span className="font-mono">{independence.effective_corroboration}</span>
        {syndicated
          ? ` · ${independence.syndicated_groups} syndicated ${
              independence.syndicated_groups === 1 ? 'group' : 'groups'
            }`
          : ''}
      </p>
      {syndicated ? (
        <p className="mt-1 text-[10.5px] leading-snug text-warn">{independence.note}</p>
      ) : null}
      {tierRows.length ? (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[9px] uppercase tracking-[0.1em] text-muted">authority</span>
          {tierRows.map(([tier, cls]) => (
            <span
              key={tier}
              className={`rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${cls}`}
              title={quality.sources
                ?.filter((row) => row.authority_tier === tier)
                .flatMap((row) => row.signals || [])
                .join(', ')}
            >
              {tiers[tier]} {tier}
            </span>
          ))}
          {Number(quality.official_sources || 0) > 0 ? (
            <span className="text-[10px] text-muted">{quality.official_sources} official</span>
          ) : null}
          {Number(quality.first_party_sources || 0) > 0 ? (
            <span className="text-[10px] text-muted">
              {quality.first_party_sources} first-party
            </span>
          ) : null}
        </div>
      ) : null}
    </section>
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
    <div className="case-panel overflow-hidden" aria-live="polite">
      <header className="case-panel-head">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="case-note">
              claim {String((claim.ordinal ?? 0) + 1).padStart(2, '0')} ·{' '}
              {claim.time_sensitive ? 'time-sensitive · ' : ''}
              {claim.volatility_class || 'unclassified'}
              {claim.rule ? ` · rule ${claim.rule}` : ''}
            </p>
            <h2 className="case-claim">{claim.claim_text}</h2>
          </div>
          <span className={`case-stamp ${STAMP[meta.tone] || STAMP.muted}`}>
            <span className="case-stamp-word">{meta.label}</span>
            <code>{claim.reason_code || 'pending'}</code>
          </span>
        </div>
        <p className="case-why">{claim.explanation || meta.note}</p>
        {/* The rule note is a restatement of what the verdict means. When the
            stored explanation already says it, printing both is noise. */}
        {claim.explanation && !claim.explanation.includes(meta.note) ? (
          <p className="case-note">{meta.note}</p>
        ) : null}
      </header>

      <dl className="claim-facts">
        <div>
          <dt>subject</dt>
          <dd>{claim.subject || '—'}</dd>
        </div>
        <div>
          <dt>predicate</dt>
          <dd>{claim.predicate || '—'}</dd>
        </div>
        <div>
          <dt>value asserted</dt>
          <dd className="mono">{claim.object_raw || '—'}</dd>
        </div>
      </dl>

      <div className="case-body">
        {claim.status === 'changed' ? <ValueChange claim={claim} /> : null}

        <Freshness claim={claim} />

        <Corroboration data={claim.corroboration} />

        <div>
          <Tabs
            label="Claim detail"
            value={tab}
            onChange={setTab}
            tabs={[
              { id: 'evidence', label: 'Evidence', count: evidence.length },
              { id: 'proof', label: 'Proof graph', count: evidence.length },
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
            <TabPanel id="proof" active={tab === 'proof'}>
              <ProofGraph claim={claim} evidence={evidence} />
            </TabPanel>

            <TabPanel id="history" active={tab === 'history'}>
              <ol>
                {(claim.timeline || []).map((row, index) => (
                  <li key={index} className="line-row">
                    <span className="min-w-0">
                      <span className="font-mono text-[10.5px] text-ink-2">
                        {row.previous_status || 'pending'} → {row.status}
                      </span>
                      <span className="ml-2 text-[10.5px] text-muted">
                        {reasonLabel(row.reason_code)}
                      </span>
                    </span>
                    <span className="shrink-0 font-mono text-[10px] text-muted">
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
                  <ul className="mt-2">
                    {claim.assertions.map((row) => (
                      <li key={row.id} className="line-row">
                        <span className="min-w-0">
                          <span className="font-mono text-ink-2">{row.value_raw || '—'}</span>
                          <span className="ml-2 text-[10.5px] text-muted">
                            {row.is_original ? 'original' : 'replacement'}
                          </span>
                        </span>
                        <span className="shrink-0 font-mono text-[10px] text-muted">
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
      </div>

      <footer className="case-foot">
        <span>
          {claim.next_check_at
            ? `next recommended check ${shortDate(claim.next_check_at)}`
            : 'no recheck scheduled'}
        </span>
        <CopyButton text={claim.claim_text} label="Copy claim" />
      </footer>
    </div>
  );
}
