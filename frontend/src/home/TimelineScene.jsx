import { useMemo, useState } from 'react';
import { TIMELINE_SCENARIOS, TIMESTAMPS, VERDICTS } from './demo.js';

const TONE = {
  ok: 'text-ok',
  info: 'text-info',
  danger: 'text-danger',
  muted: 'text-muted',
};

const MS_DAY = 86_400_000;

function daysBetween(a, b) {
  const from = Date.parse(`${a}T00:00:00Z`);
  const to = Date.parse(`${b}T00:00:00Z`);
  if (Number.isNaN(from) || Number.isNaN(to)) return null;
  return Math.round((to - from) / MS_DAY);
}

function signed(days) {
  if (days === null) return 'no date';
  if (days === 0) return 'same day';
  return `${days > 0 ? '+' : '−'}${Math.abs(days)} day${Math.abs(days) === 1 ? '' : 's'}`;
}

/**
 * Four clocks.
 *
 * The engine's whole argument is that these are not the same clock, so the
 * section is built as a comparison rather than a diagram: pick a run, read the
 * four timestamps, then read the one inequality that assigns the status. The
 * second scenario is a page published *after* generation that describes an
 * *older* fact — the case a publication-date rule gets wrong.
 */
export default function TimelineScene() {
  const [active, setActive] = useState(0);
  const scenario = TIMELINE_SCENARIOS[active];
  const verdict = VERDICTS[scenario.status];

  const delta = useMemo(
    () => daysBetween(scenario.generated, scenario.event),
    [scenario.generated, scenario.event],
  );

  const after = delta !== null && delta > 0;
  // 60 days maps to half the track; anything longer sits at the edge. The
  // generation mark stays pinned at 50% so "after" always reads rightward.
  const offset = delta === null ? 0 : Math.min(50, Math.max(4, (Math.abs(delta) / 60) * 50));
  const fillStyle = after
    ? { left: '50%', width: `${offset}%` }
    : { left: `${50 - offset}%`, width: `${offset}%` };
  const eventStyle = { left: `${after ? 50 + offset : 50 - offset}%` };

  return (
    <div className="timeline-scene">
      <div className="scene-switch" role="group" aria-label="Choose an example run">
        {TIMELINE_SCENARIOS.map((entry, index) => (
          <button
            key={entry.id}
            type="button"
            aria-pressed={index === active}
            onClick={() => setActive(index)}
            className={`scene-switch-btn ${index === active ? 'is-on' : ''}`}
          >
            <span className="scene-switch-num">{String(index + 1).padStart(2, '0')}</span>
            {entry.label}
          </button>
        ))}
      </div>

      <ol className="clock-strip" key={scenario.id}>
        {TIMESTAMPS.map((stamp) => {
          const value = scenario[stamp.key];
          const isEvent = stamp.key === 'event';
          const isGenerated = stamp.key === 'generated';
          return (
            <li
              key={stamp.key}
              className={`clock-cell ${isEvent ? 'is-event' : ''} ${isGenerated ? 'is-generated' : ''}`}
            >
              <span className="clock-rule" aria-hidden="true" />
              <span className="clock-field">{stamp.field}</span>
              <span className="clock-label">{stamp.label}</span>
              <span className="clock-value">{value}</span>
              <span className="clock-line">{stamp.line}</span>
            </li>
          );
        })}
      </ol>

      <div className="inequality" aria-live="polite">
        <div className="ineq-head">
          <span className="ineq-caption">event date − answer generated</span>
          <span className={`ineq-delta ${after ? 'is-after' : 'is-before'}`}>{signed(delta)}</span>
        </div>
        <div className="delta-track" aria-hidden="true">
          <span className="delta-axis" />
          <span className={`delta-fill ${after ? 'is-after' : 'is-before'}`} style={fillStyle} />
          <span className="delta-mark is-generated" />
          <span className={`delta-mark ${after ? 'is-after' : 'is-before'}`} style={eventStyle} />
          <span className="delta-mid" />
        </div>
        <div className="ineq-foot">
          <code>
            {scenario.event} {after ? '>' : '≤'} {scenario.generated}
          </code>
          <span className={`ineq-status ${TONE[verdict.tone]}`}>
            {verdict.label} · {scenario.reason}
          </span>
        </div>
        <p className="ineq-reading">{scenario.reading}</p>
        <p className="ineq-note">
          Published {scenario.published} · retrieved {scenario.retrieved} — both after generation, and
          neither is allowed to decide a change.
        </p>
      </div>
    </div>
  );
}
