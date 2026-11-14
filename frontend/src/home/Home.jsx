import { useRef } from 'react';
import { Link } from 'react-router-dom';
import Dossier from './Dossier.jsx';
import Ledger from './Ledger.jsx';
import { Counter, ScrollProgress, Ticker, useScrolled } from './Motion.jsx';
import StrataField from './StrataField.jsx';
import TimelineScene from './TimelineScene.jsx';
import { CAPABILITIES, RULE_VERSION, STATS, STEPS, TICKER, VERDICTS } from './demo.js';
import { useReveal } from './useReveal.js';
import './home.css';

const ORDER = ['supported', 'changed', 'contradicted', 'unverifiable'];

const TONE = {
  ok: 'text-ok',
  info: 'text-info',
  danger: 'text-danger',
  muted: 'text-muted',
};

const REASONS = {
  supported: ['supported_by_source'],
  changed: ['value_changed_after_generation', 'generation_time_unknown'],
  contradicted: ['contradicted_by_source', 'temporal_ambiguity'],
  unverifiable: ['no_source', 'no_entailment', 'conflicting_evidence', 'scope_mismatch'],
};

function Brand() {
  return (
    <Link to="/" className="brand" aria-label="Afterfact — home">
      <img src="/tee-mark.svg" width="30" height="30" alt="" className="brand-mark" />
      <span className="brand-text">
        <span className="brand-name">Afterfact</span>
        <span className="brand-sub">claim-level verification</span>
      </span>
    </Link>
  );
}

export default function Home() {
  useReveal();
  const scrolled = useScrolled(18);
  const heroRef = useRef(null);
  const glowRef = useRef(null);

  // One pointer handler for the whole hero: it moves the glow layer and nothing
  // else, so it never triggers a React render.
  const onHeroPointer = (event) => {
    const node = heroRef.current;
    const glow = glowRef.current;
    if (!node || !glow) return;
    const rect = node.getBoundingClientRect();
    glow.style.left = `${event.clientX - rect.left}px`;
    glow.style.top = `${event.clientY - rect.top}px`;
  };

  return (
    <div className="home">
      <div className="grain" aria-hidden="true" />
      <ScrollProgress />
      <a className="skip" href="#main">
        Skip to content
      </a>

      <header className={`home-nav ${scrolled ? 'is-scrolled' : ''}`}>
        <Brand />
        <nav className="home-links" aria-label="Sections">
          <a href="#timeline">The four clocks</a>
          <a href="#ledger">Evidence ledger</a>
          <a href="#mechanism">How it decides</a>
        </nav>
        <div className="home-nav-actions">
          <Link to="/runs" className="btn-ghost btn-xs">
            Runs
          </Link>
          <Link to="/check" className="btn-accent btn-xs">
            Run a check
          </Link>
        </div>
      </header>

      <main id="main">
        <section className="hero" ref={heroRef} onPointerMove={onHeroPointer}>
          <StrataField />
          <div className="hero-glow" ref={glowRef} aria-hidden="true" />
          <div className="hero-grid">
            <div className="hero-copy">
              <p className="hero-eyebrow">
                <span className="hero-eyebrow-dot" aria-hidden="true" />
                Claim-level verification
                <span className="hero-rule" aria-hidden="true" />
                <span className="font-mono">rule {RULE_VERSION}</span>
              </p>
              <h1 className="hero-title">
                <span className="hero-line">
                  <span className="hero-line-in">Every claim</span>
                </span>
                <span className="hero-line">
                  <span className="hero-line-in is-accent">
                    <em>leaves a trace.</em>
                  </span>
                </span>
              </h1>
              <p className="hero-sub">
                Paste an AI answer and the moment it was written. It comes back as atomic claims, with
                the exact quoted text from stored source revisions and one verdict per claim — decided
                by dates, not by a model’s opinion.
              </p>
              <div className="hero-actions">
                <Link to="/check" className="btn-accent hero-cta">
                  Verify an answer
                  <span aria-hidden="true">→</span>
                </Link>
                <a href="#ledger" className="btn-outline hero-cta">
                  Read a real verdict
                </a>
              </div>
            </div>
            <div className="hero-figure">
              <Dossier />
            </div>
          </div>
          <div className="hero-rail">
            <span>
              <b>18</b> gold cases · 19 claims
            </span>
            <span>
              <b>4</b> verdicts, no fifth
            </span>
            <span>
              <b>0</b> verdicts written by a model
            </span>
            <span className="hero-rail-end">supported · changed · contradicted · unverifiable</span>
          </div>
        </section>

        <Ticker items={TICKER} />

        <section id="timeline" className="scene" data-reveal>
          <div className="scene-head">
            <div className="scene-head-main">
              <p className="scene-index">01 / the four clocks</p>
              <h2 className="scene-title">
                One answer.
                <br />
                Four clocks.
              </h2>
            </div>
            <p className="scene-lede">
              A verdict turns on which clock you read. Publication is when the page spoke; the event
              date is when the fact moved. Only one of them is allowed to make a claim stale — and if
              the event date is unknown, the cell says <span className="font-mono">missing</span>.
            </p>
          </div>
          <TimelineScene />
        </section>

        <section id="ledger" className="scene scene-ledger" data-reveal>
          <div className="scene-head">
            <div className="scene-head-main">
              <p className="scene-index">02 / evidence ledger</p>
              <h2 className="scene-title">
                Four rows that look
                <br />
                like one sentence.
              </h2>
            </div>
            <p className="scene-lede">
              These come from the graded gold set. Four of them are the same price claim about the same
              product; only one is stale, one is already wrong, and one was never settled at all. Open a
              row to read the quote that decided it.
            </p>
          </div>
          <Ledger />
        </section>

        <section className="scene scene-verdicts" data-reveal>
          <div className="scene-head">
            <div className="scene-head-main">
              <p className="scene-index">03 / the decision table</p>
              <h2 className="scene-title">
                Four verdicts.
                <br />
                <em>No fifth.</em>
              </h2>
            </div>
            <p className="scene-lede">
              There is no “partly true”. A failed support is not a contradiction, a settled refutation
              with no replacement is not a change, and abstention is a recorded outcome rather than a
              quiet failure.
            </p>
          </div>

          <ul className="verdict-list">
            {ORDER.map((key) => {
              const meta = VERDICTS[key];
              return (
                <li key={key} className="verdict-item">
                  <span className={`verdict-name ${TONE[meta.tone]}`}>{meta.label}</span>
                  <p className="verdict-line">{meta.line}</p>
                  <div className="verdict-codes">
                    {REASONS[key].map((code) => (
                      <code key={code}>{code}</code>
                    ))}
                  </div>
                </li>
              );
            })}
          </ul>

          <dl className="stat-band">
            {STATS.map((stat) => (
              <div key={stat.label} className="stat">
                <dt>{stat.label}</dt>
                <dd>
                  <span className="stat-value">
                    <Counter value={stat.value} suffix={stat.suffix} decimals={stat.decimals} />
                  </span>
                  <span className="stat-detail">{stat.detail}</span>
                  <span className="stat-source">{stat.source}</span>
                </dd>
              </div>
            ))}
          </dl>
          <p className="stat-foot">
            Verdict accuracy and assertion recall are separate measurements, run separately. Neither
            number is a claim of absolute truth.
          </p>
        </section>

        <section id="mechanism" className="scene scene-mechanism" data-reveal>
          <div className="mechanism-grid">
            <div className="mechanism-aside">
              <p className="scene-index">04 / mechanism</p>
              <h2 className="scene-title">
                The model proposes.
                <br />
                <em>Code decides.</em>
              </h2>
              <p className="scene-lede">
                Five steps. The model appears once, and it is never allowed to write a status.
              </p>
              <Link to="/check" className="btn-primary mechanism-cta">
                Start with an answer
              </Link>
            </div>

            <ol className="step-list">
              {STEPS.map((step) => (
                <li key={step.n} className="step">
                  <span className="step-n">{step.n}</span>
                  <div>
                    <h3 className="step-title">{step.title}</h3>
                    <p className="step-body">{step.body}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>

          <ul className="capability-grid" aria-label="What a run records">
            {CAPABILITIES.map(([name, line]) => (
              <li key={name}>
                <span className="capability-name">{name}</span>
                <span className="capability-line">{line}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="finale" data-reveal>
          <p className="scene-index">05 / open the verifier</p>
          <h2 className="finale-title">
            Paste an answer.
            <br />
            <em>See what survives.</em>
          </h2>
          <p className="finale-sub">
            No account, no configuration. A run takes as long as its sources take, and it reports every
            stage — including the ones that fail.
          </p>
          <div className="hero-actions">
            <Link to="/check" className="btn-accent hero-cta">
              Verify an answer
              <span aria-hidden="true">→</span>
            </Link>
            <Link to="/runs" className="btn-outline hero-cta">
              Browse past runs
            </Link>
          </div>
        </section>
      </main>

      <footer className="home-foot">
        <div className="home-foot-brand">
          <Brand />
          <p>
            A verdict assesses the cited sources. It is not a statement of absolute truth.
          </p>
        </div>
        <div className="home-foot-cols">
          <div>
            <span className="foot-head">Product</span>
            <Link to="/check">New check</Link>
            <Link to="/runs">Runs</Link>
            <Link to="/watchlist">Watchlist</Link>
          </div>
          <div>
            <span className="foot-head">Page</span>
            <a href="#timeline">The four clocks</a>
            <a href="#ledger">Evidence ledger</a>
            <a href="#mechanism">Mechanism</a>
          </div>
          <div>
            <span className="foot-head">Rules</span>
            <span className="font-mono">rule {RULE_VERSION}</span>
            <span className="font-mono">18 gold cases</span>
            <span className="font-mono">hermetic · postgres · live</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
