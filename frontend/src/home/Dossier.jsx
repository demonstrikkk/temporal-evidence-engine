import { useEffect, useRef, useState } from 'react';
import { CASES, VERDICTS } from './demo.js';

const TONE = {
  ok: { pill: 'text-ok border-ok/40 bg-ok-soft', dot: 'bg-ok' },
  info: { pill: 'text-info border-info/40 bg-info-soft', dot: 'bg-info' },
  danger: { pill: 'text-danger border-danger/40 bg-danger-soft', dot: 'bg-danger' },
  muted: { pill: 'text-muted border-rule-2 bg-warm/60', dot: 'bg-muted' },
};

const STEP_MS = 2600;

function Quote({ text, highlight }) {
  if (!highlight) return <>{text}</>;
  const at = text.indexOf(highlight);
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <mark className="proof">{highlight}</mark>
      {text.slice(at + highlight.length)}
    </>
  );
}

function clock(value) {
  if (!value) return 'missing';
  const text = String(value);
  if (text.length <= 10) return text;
  return `${text.slice(0, 10)} ${text.slice(11, 16)}z`;
}

/**
 * The verdict deck.
 *
 * Four cases, one per outcome, switched by a real tablist. The point is not to
 * show four colours; it is that three of these answers contain a price and only
 * one of them is stale. The dates under each case are the explanation.
 *
 * "Play" steps through the four cases once. It is user-initiated and stoppable,
 * which is the only kind of auto-advance that is acceptable on a page like this.
 */
export default function Dossier() {
  const [active, setActive] = useState(1);
  const [playing, setPlaying] = useState(false);
  const tabs = useRef([]);
  const timer = useRef(0);
  const item = CASES[active];
  const verdict = VERDICTS[item.status];
  const tone = TONE[verdict.tone];

  useEffect(() => {
    if (!playing) return undefined;
    timer.current = setInterval(() => {
      setActive((current) => {
        if (current >= CASES.length - 1) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, STEP_MS);
    return () => clearInterval(timer.current);
  }, [playing]);

  const stop = () => setPlaying(false);

  const focusTab = (index) => {
    const next = (index + CASES.length) % CASES.length;
    setActive(next);
    tabs.current[next]?.focus();
  };

  const onKeyDown = (event) => {
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      focusTab(active + 1);
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault();
      focusTab(active - 1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      focusTab(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      focusTab(CASES.length - 1);
    }
  };

  return (
    <figure className="dossier" aria-labelledby="dossier-title">
      <figcaption className="dossier-head">
        <span className="dossier-index">case file 02 / 04</span>
        <h2 id="dossier-title" className="dossier-title">
          One answer, four possible fates
        </h2>
        <button
          type="button"
          className={`dossier-play ${playing ? 'is-on' : ''}`}
          onClick={() => (playing ? stop() : setPlaying(true))}
          aria-pressed={playing}
        >
          <span className="dossier-play-icon" aria-hidden="true">
            {playing ? '■' : '▶'}
          </span>
          {playing ? 'Stop' : 'Play'}
        </button>
      </figcaption>

      <div
        className="deck-tabs"
        role="tablist"
        aria-label="Example claims, one per verdict"
        onKeyDown={onKeyDown}
      >
        {CASES.map((entry, index) => {
          const meta = VERDICTS[entry.status];
          const selected = index === active;
          return (
            <button
              key={entry.id}
              ref={(node) => {
                tabs.current[index] = node;
              }}
              type="button"
              role="tab"
              id={`deck-tab-${entry.id}`}
              aria-selected={selected}
              aria-controls="deck-panel"
              tabIndex={selected ? 0 : -1}
              onClick={() => {
                stop();
                setActive(index);
              }}
              className={`deck-tab ${selected ? 'is-on' : ''}`}
            >
              <span className={`deck-dot ${TONE[meta.tone].dot}`} aria-hidden="true" />
              {meta.label}
            </button>
          );
        })}
        <span
          className="deck-underline"
          style={{ left: `${(active * 100) / CASES.length}%` }}
          aria-hidden="true"
        />
      </div>

      <div
        key={item.id}
        id="deck-panel"
        role="tabpanel"
        aria-labelledby={`deck-tab-${item.id}`}
        className="dossier-panel"
        tabIndex={0}
      >
        <div className="dossier-row">
          <p className="dossier-prompt">
            <span className="dossier-key">answer</span>
            {item.claim}
          </p>
          <div className={`verdict-stamp ${tone.pill}`}>
            <span className="verdict-word">{verdict.label}</span>
            <code className="verdict-code">{item.reason}</code>
          </div>
        </div>

        <div className="quote-block">
          <span className="quote-key">quoted from the stored revision</span>
          <blockquote className="quote-text">
            <Quote text={item.quote} highlight={item.highlight} />
          </blockquote>
          <p className="quote-source">
            <span className="font-mono">{item.source.url}</span>
            <span aria-hidden="true">·</span>
            <span>{item.source.title}</span>
          </p>
          <span className="quote-scan" aria-hidden="true" />
        </div>

        <dl className="clock-grid">
          <div>
            <dt>answer generated</dt>
            <dd>{clock(item.generatedAt)}</dd>
          </div>
          <div>
            <dt>source published</dt>
            <dd>{item.publishedDate}</dd>
          </div>
          <div className={item.eventDate ? 'is-event' : ''}>
            <dt>fact changed</dt>
            <dd>{item.eventDate || 'missing'}</dd>
          </div>
          <div>
            <dt>retrieved</dt>
            <dd>{clock(item.retrievedAt)}</dd>
          </div>
        </dl>

        <div className="settle-line">
          <code>
            {item.proposal} → {item.settlement}
          </code>
        </div>

        <p className="dossier-why">{item.why}</p>
        <p className="dossier-not">
          <span aria-hidden="true">✕ </span>
          {item.notThis}
        </p>
      </div>
    </figure>
  );
}
