/**
 * The run ledger. Every stage is an event, including the ones that failed.
 *
 * Newest first on purpose: a run is watched live, and the thing that just
 * happened should not require scrolling past everything that already did.
 *
 * The stream state is the panel's aside, not a separate chip row: "streaming"
 * is the answer to "can I trust this list to be current".
 */
import { useMemo, useState } from 'react';
import { describeEvent, isProblem, timeOnly } from './model.js';
import { SectionHeading } from './ui.jsx';

const TONE_DOT = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  muted: 'bg-rule-2',
};

const STREAM_COPY = {
  connecting: 'connecting',
  live: 'streaming',
  polling: 'polling',
  ended: 'closed',
  closed: 'closed',
};

export default function EventRail({ events, streamState = 'connecting' }) {
  const [problemsOnly, setProblemsOnly] = useState(false);
  const rows = useMemo(() => {
    const list = [...(events || [])].reverse();
    return problemsOnly ? list.filter(isProblem) : list;
  }, [events, problemsOnly]);

  const problemCount = useMemo(() => (events || []).filter(isProblem).length, [events]);

  return (
    <section className="surface px-4 py-3.5" aria-label="Run ledger">
      <SectionHeading
        aside={
          <span className="inline-flex items-center gap-1.5">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                streamState === 'live' ? 'bg-ok' : 'bg-rule-2'
              }`}
              aria-hidden="true"
            />
            {STREAM_COPY[streamState] || streamState}
          </span>
        }
      >
        Run ledger
        <span className="ml-1 text-muted">({events?.length || 0})</span>
      </SectionHeading>

      <div className="event-tools">
        <p className="font-mono text-[9px] uppercase tracking-[0.14em] text-muted">
          newest first · each stage emits one event
        </p>
        <label className="flex items-center gap-1.5 text-[11px] text-ink-2">
          <input
            type="checkbox"
            className="h-3 w-3 accent-[rgb(var(--accent))]"
            checked={problemsOnly}
            onChange={(event) => setProblemsOnly(event.target.checked)}
          />
          Problems only
          {problemCount ? (
            <span className="rounded-full bg-warn-soft px-1.5 text-[10px] text-warn">
              {problemCount}
            </span>
          ) : null}
        </label>
      </div>

      <div className="event-list">
        {rows.length ? (
          <ol>
            {rows.map((event, index) => {
              const view = describeEvent(event);
              return (
                <li key={event.sequence ?? `${view.type}-${index}`} className="event-row">
                  <span className="event-time">{timeOnly(event.created_at) || '—'}</span>
                  <span
                    className={`event-dot ${TONE_DOT[view.tone] || TONE_DOT.muted}`}
                    aria-hidden="true"
                  />
                  <span className="min-w-0">
                    <span className="event-label">{view.label}</span>
                    {view.detail ? (
                      <span className="event-detail" title={view.detail}>
                        {view.detail}
                      </span>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ol>
        ) : (
          <p className="py-4 text-center text-[11.5px] text-muted">
            {problemsOnly ? 'No failures were reported.' : 'Waiting for the first event…'}
          </p>
        )}
      </div>
    </section>
  );
}
