/**
 * The answer, with each located claim marked in the sentence it came from.
 *
 * The spans are byte offsets the extractor stored, not a search performed here.
 * If an offset does not land exactly on its text, the mark is not drawn — a
 * highlight that points at the wrong words is worse than no highlight.
 */
import { buildSegments, MARK, statusMeta } from './model.js';
import { StatusPill } from './ui.jsx';

export default function AnswerSpans({ answer, claims, selectedId, onSelect }) {
  const segments = buildSegments(answer, claims);
  const present = [...new Set((claims || []).map((claim) => claim.status || 'pending'))].filter(
    (status) => status !== 'pending',
  );

  return (
    <div className="space-y-3">
      <p className="whitespace-pre-wrap font-display text-[17.5px] leading-[1.65] text-ink">
        {segments.map((segment, index) => {
          if (!segment.claim) return <span key={index}>{segment.text}</span>;
          const claim = segment.claim;
          const status = claim.status || 'pending';
          const selected = claim.id === selectedId;
          return (
            <button
              key={claim.id || index}
              type="button"
              onClick={() => onSelect?.(claim.id)}
              aria-pressed={selected}
              title={`${statusMeta(status).label}: ${claim.claim_text}`}
              className={`claim-span inline cursor-pointer rounded-xs border-b-2 px-0.5 text-left align-baseline transition-colors duration-150 focus:outline-none focus-visible:ring-4 focus-visible:ring-[var(--accent-ring)] ${
                MARK[status] || MARK.pending
              } ${selected ? 'ring-2 ring-accent/45' : ''}`}
            >
              {segment.text}
            </button>
          );
        })}
      </p>

      {present.length ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-rule/70 pt-2.5">
          <span className="text-[10.5px] uppercase tracking-[0.12em] text-muted">Marked spans</span>
          {present.map((status) => (
            <StatusPill key={status} status={status} size="sm" />
          ))}
          <span className="text-[10.5px] text-muted">Click a span to open its evidence.</span>
        </div>
      ) : (
        <p className="border-t border-rule/70 pt-2.5 text-[11px] text-muted">
          No claim span could be located in this text. The register below is still authoritative.
        </p>
      )}
    </div>
  );
}
