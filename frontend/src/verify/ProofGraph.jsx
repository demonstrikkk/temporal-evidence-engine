/**
 * ProofGraph — the claim and the evidence rows attached to it.
 *
 * The graph is a spine: the claim at the top, one branch per stored evidence
 * row, and the event date in the gutter. That order is the product's argument —
 * a fact has a date of its own, separate from the date the page was published —
 * so the dated rows are laid out on that line and the undated ones are grouped
 * after it and labelled, rather than being given a position they cannot earn.
 *
 * Deliberately not a fixed-width SVG. The inspector column is ~390px, a quote
 * wraps, and a viewBox sized to the node count clipped the last row. A grid can
 * wrap; a canvas with hard-coded coordinates cannot.
 */
import { useMemo } from 'react';
import { buildProofNodes, proofSummary } from './proofModel.js';
import { shortDate, shortUrl, statusMeta } from './model.js';
import { Empty } from './ui.jsx';

const TONE_TEXT = {
  ok: 'text-ok',
  warn: 'text-warn',
  danger: 'text-danger',
  info: 'text-info',
  muted: 'text-muted',
};

function Branch({ node }) {
  const when = node.eventMissing ? 'no event date' : shortDate(node.eventDate);
  const sourceLabel = node.publisher || shortUrl(node.sourceUrl, 34);
  return (
    <li className={`proof-branch is-${node.tone}`}>
      <span
        className={`proof-when${node.eventMissing ? ' is-missing' : ''}`}
        title={
          node.eventMissing
            ? 'The source did not state when this fact changed. Publication date is not used as a substitute.'
            : `event date, ${node.eventStatus}`
        }
      >
        {when}
      </span>
      <span className="proof-rail" aria-hidden="true">
        <span className="proof-dot" />
      </span>
      <div className="proof-node">
        <div className="proof-node-head">
          <span className="proof-rel">{node.relationshipLabel}</span>
          <span className="proof-state">
            {node.accepted ? 'settled' : 'not settled'}
          </span>
          {node.replacement ? (
            <span className="proof-state" title="replacement value asserted by this source">
              → {node.replacement}
            </span>
          ) : null}
        </div>
        {node.quote ? (
          <blockquote className="proof-quote">“{node.quote}”</blockquote>
        ) : (
          <p className="proof-quote is-missing">No quote was recorded for this source.</p>
        )}
        <span className="proof-source">
          {node.sourceUrl ? (
            <a href={node.sourceUrl} target="_blank" rel="noreferrer noopener">
              {sourceLabel}
            </a>
          ) : (
            sourceLabel || 'source unknown'
          )}
          {node.publishedDate ? ` · published ${shortDate(node.publishedDate)}` : ''}
          {node.retrievedAt ? ` · retrieved ${shortDate(node.retrievedAt)}` : ''}
        </span>
      </div>
    </li>
  );
}

export default function ProofGraph({ claim, evidence = [] }) {
  const model = useMemo(() => buildProofNodes(claim, evidence), [claim, evidence]);

  if (!claim) {
    return (
      <div className="surface p-4 text-[12px] text-muted">
        Select a claim to see its evidence graph.
      </div>
    );
  }

  if (!model.nodes.length) {
    return (
      <Empty title="No evidence branch">
        Nothing was attached to this claim, so the graph has one node and no edges. The verdict
        above says why.
      </Empty>
    );
  }

  const meta = statusMeta(model.claimStatus);

  return (
    <div className="proof" aria-label="Temporal proof graph">
      <div className="proof-head">
        <span className="proof-summary">{proofSummary(model)}</span>
        <span className={`font-mono text-[9px] uppercase tracking-[0.1em] ${TONE_TEXT[meta.tone] || 'text-muted'}`}>
          {meta.label}
        </span>
      </div>

      <div className="proof-claim">
        <p className="proof-claim-text">{model.claimText}</p>
        <p className="proof-claim-meta">
          <span>{claim.object_raw ? `asserted ${claim.object_raw}` : 'no asserted value'}</span>
          <span>{model.contradicting ? `${model.contradicting} contradicting` : 'no contradiction'}</span>
        </p>
      </div>

      <ol className="proof-spine">
        {model.nodes.map((node) => (
          <Branch key={node.id} node={node} />
        ))}
      </ol>

      <div className="proof-legend" aria-label="Legend">
        <span>
          <i style={{ background: 'rgb(var(--ok))' }} aria-hidden="true" />
          supports
        </span>
        <span>
          <i style={{ background: 'rgb(var(--danger))' }} aria-hidden="true" />
          contradicts
        </span>
        <span>
          <i style={{ background: 'rgb(var(--info))' }} aria-hidden="true" />
          mentions only
        </span>
        <span>
          <i style={{ background: 'rgb(var(--rule-2))' }} aria-hidden="true" />
          not settled
        </span>
      </div>

      <p className="proof-note">
        Left to right is time: branches are ordered by <b>event date</b>, the date the source says
        the fact changed. {model.undated ? `${model.undated} row${model.undated === 1 ? '' : 's'} state no event date and sit at the end — publication date is never used in its place.` : 'Every row states an event date.'}
      </p>
    </div>
  );
}
