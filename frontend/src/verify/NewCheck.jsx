/**
 * Intake. The only thing the product asks for is an answer and when it was
 * generated — everything else is derived.
 *
 * "When it was generated" is not optional decoration: `changed` is defined as a
 * settled replacement whose event date is after generation. Without a stated
 * generation time the engine can still find contradictions, but it refuses to
 * call anything stale, and says so.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { ago, shortDate } from './model.js';
import { Field, RunStatePill, SectionHeading } from './ui.jsx';

function toLocalInput(date) {
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

const EXAMPLES = [
  {
    id: 'price',
    label: 'A price that moves',
    prompt: 'What does ChatGPT Plus cost, and where is OpenAI based?',
    generatedAt: '2026-10-01T09:00',
    answer:
      'ChatGPT Plus costs $20 per month. OpenAI is headquartered in San Francisco. ' +
      'The subscription includes access to the latest models and higher message limits.',
  },
  {
    id: 'plan',
    label: 'A product plan',
    prompt: 'Summarise Cursor’s paid plans.',
    generatedAt: '2026-09-15T14:30',
    answer:
      'Cursor Pro costs $20 per month and includes 500 fast requests. ' +
      'The Business plan costs $40 per user per month and adds centralised billing ' +
      'and admin controls.',
  },
  {
    id: 'stable',
    label: 'A stable fact',
    prompt: 'Where is the Eiffel Tower and how tall is it?',
    generatedAt: '2026-08-02T18:05',
    answer:
      'The Eiffel Tower is in Paris, France. It is 330 metres tall and was completed in 1889.',
  },
];

const RULES = [
  ['R1', 'No locatable quote', 'unverifiable'],
  ['R2', 'A mention is not evidence', 'unverifiable'],
  ['R3', 'A settled refutation alone', 'contradicted'],
  ['R7', 'Two primary sources disagree', 'unverifiable'],
  ['R6', 'Ordering needs event dates', 'contradicted'],
  ['R5', 'Later settled replacement', 'changed'],
  ['R4', 'Refutation older than support', 'supported'],
];

const VERDICT_TONE = {
  unverifiable: 'text-muted',
  contradicted: 'text-danger',
  changed: 'text-info',
  supported: 'text-ok',
};

export default function NewCheck() {
  const navigate = useNavigate();
  const [answer, setAnswer] = useState('');
  const [prompt, setPrompt] = useState('');
  const [model, setModel] = useState('');
  const [generatedAt, setGeneratedAt] = useState(toLocalInput(new Date()));
  const [unknownTime, setUnknownTime] = useState(false);
  const [skipNonFactual, setSkipNonFactual] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [recent, setRecent] = useState([]);

  useEffect(() => {
    let alive = true;
    api.verify
      .runs()
      .then((data) => alive && setRecent((data.runs || []).slice(0, 5)))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // Counted here, not sent: the metrics are a reading aid, and the backend
  // decides what a sentence actually is.
  const metrics = useMemo(() => {
    const text = answer.trim();
    return {
      chars: answer.length,
      words: text ? text.split(/\s+/).length : 0,
      sentences: text ? (text.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || []).length : 0,
    };
  }, [answer]);

  const loadExample = (example) => {
    setAnswer(example.answer);
    setPrompt(example.prompt);
    setGeneratedAt(example.generatedAt);
    setUnknownTime(false);
    setError(null);
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!answer.trim()) {
      setError(new Error('Paste the answer you want checked.'));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body = {
        answer: answer.trim(),
        prompt: prompt.trim(),
        model: model.trim(),
        skip_non_factual: skipNonFactual,
        generated_at: unknownTime ? null : new Date(generatedAt).toISOString(),
        generated_at_source: unknownTime ? 'unknown' : 'user_supplied',
      };
      if (!unknownTime && Number.isNaN(new Date(generatedAt).getTime())) {
        throw new Error('That generation time is not a valid date.');
      }
      const created = await api.verify.createRun(body);
      navigate(`/runs/${created.run_id}`);
    } catch (err) {
      setError(err);
      setSubmitting(false);
    }
  };

  return (
    <div className="animate-fade-up space-y-6">
      <header className="page-head">
        <div>
          <p className="eyebrow">Intake</p>
          <h1 className="page-title">
            An answer is a set of claims.
            <br />
            <em>Check each one.</em>
          </h1>
          <p className="page-lede">
            Paste an AI-generated answer and the time it was produced. The engine splits it into
            atomic claims, researches them live, and settles every quote against the exact stored
            source text — with the event date separated from the publication date, so a new article
            is never mistaken for a new fact.
          </p>
        </div>
        <p className="case-crumb hidden lg:flex">
          <span>requires</span>
          <span aria-hidden="true">·</span>
          <span>answer text + generation time</span>
        </p>
      </header>

      {/* An explicit single column below lg. A bare grid's implicit auto track
          sizes to max-content and pushes the form wider than the viewport at
          390px; minmax(0,1fr) keeps the page inside the screen. */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
        <form onSubmit={submit} className="intake-panel" aria-label="New verification">
          <div className="intake-bar">
            <span className="intake-bar-label">the answer</span>
            <div className="intake-metrics">
              <span>
                <b>{metrics.chars}</b> chars
              </span>
              <span>
                <b>{metrics.words}</b> words
              </span>
              <span>
                <b>{metrics.sentences}</b> sentences
              </span>
            </div>
          </div>

          <div className="space-y-4 p-4">
            <Field
              label="Answer text"
              hint="One claim per sentence works best. Opinions and predictions are ignored, not failed."
            >
              <textarea
                className="input min-h-[220px] resize-y font-mono text-[12.5px] leading-relaxed"
                value={answer}
                onChange={(event) => setAnswer(event.target.value)}
                placeholder="Paste the answer here…"
                spellCheck={false}
                required
              />
            </Field>

            <div>
              <p className="label">Or open a specimen</p>
              <div className="flex flex-wrap gap-1.5">
                {EXAMPLES.map((example) => (
                  <button
                    key={example.id}
                    type="button"
                    className="specimen"
                    onClick={() => loadExample(example)}
                    title={example.prompt}
                  >
                    <span className="specimen-id">{example.id}</span>
                    {example.label}
                  </button>
                ))}
              </div>
            </div>

            <Field
              label="Original question · context only"
              hint="Optional. It helps extraction, and it is shown with the report."
            >
              <input
                className="input"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="What did you ask the model?"
              />
            </Field>

            <Field
              label="Model that wrote it · optional"
              hint="Recorded with the answer so its verdicts can be compared on the compare page. Left blank, it is reported there as a pasted answer with no model recorded."
            >
              <input
                className="input font-mono text-[12.5px]"
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder="gpt-4o, claude-sonnet-4-5, llama3.2:1b…"
                autoComplete="off"
              />
            </Field>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-[minmax(0,1fr)_auto]">
              <Field
                label="Answer generated at"
                hint={
                  unknownTime
                    ? 'Without a generation time, no claim can be marked “changed” — only supported, contradicted or unverifiable.'
                    : 'Local time. This is the reference point for “has the fact changed since?”'
                }
              >
                <input
                  type="datetime-local"
                  className="input font-mono text-[12.5px]"
                  value={generatedAt}
                  onChange={(event) => setGeneratedAt(event.target.value)}
                  disabled={unknownTime}
                  required={!unknownTime}
                />
              </Field>
              <label className="flex items-end gap-2 pb-1.5 text-[12px] text-ink-2">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 accent-[rgb(var(--accent))]"
                  checked={unknownTime}
                  onChange={(event) => setUnknownTime(event.target.checked)}
                />
                Unknown
              </label>
            </div>

            <label className="flex items-start gap-2 text-[12px] leading-snug text-ink-2">
              <input
                type="checkbox"
                className="mt-0.5 h-3.5 w-3.5 accent-[rgb(var(--accent))]"
                checked={skipNonFactual}
                onChange={(event) => setSkipNonFactual(event.target.checked)}
              />
              <span>
                Skip opinions, predictions and ambiguous statements.
                <span className="block text-[11px] text-muted">
                  They are not factual claims that failed — they are not factual claims.
                </span>
              </span>
            </label>

            {error ? (
              <div
                role="alert"
                className="rounded-sm border border-danger/30 bg-danger-soft px-3 py-2 text-[12px] text-danger"
              >
                {error.message || String(error)}
              </div>
            ) : null}
          </div>

          <div className="intake-foot">
            <button type="submit" className="btn-accent" disabled={submitting || !answer.trim()}>
              {submitting ? 'Starting…' : 'Verify this answer'}
              {submitting ? null : <span aria-hidden="true">→</span>}
            </button>
            <span className="text-[11px] leading-snug text-muted">
              Live research takes tens of seconds. The run reports every stage as it happens.
            </span>
          </div>
        </form>

        <aside className="space-y-5">
          <section className="surface px-4 py-3.5">
            <SectionHeading aside="frozen">How a verdict is decided</SectionHeading>
            <div className="mt-2">
              {RULES.map(([rule, meaning, verdict]) => (
                <div key={rule} className="rule-row">
                  <span className="rule-n">{rule}</span>
                  <span className="rule-meaning">{meaning}</span>
                  <span className={`rule-verdict ${VERDICT_TONE[verdict] || 'text-muted'}`}>
                    {verdict}
                  </span>
                </div>
              ))}
            </div>
            <p className="mt-3 border-t border-rule pt-3 text-[11px] leading-relaxed text-muted">
              First match wins. The model proposes a relationship; a deterministic gate decides
              whether the quote establishes it; the table above decides the status from stored rows.
            </p>
          </section>

          <section className="surface px-4 py-3.5">
            <SectionHeading
              aside={
                recent.length ? (
                  <Link className="link" to="/runs">
                    all runs
                  </Link>
                ) : null
              }
            >
              Recent runs
            </SectionHeading>
            {recent.length ? (
              <ul className="mt-1">
                {recent.map((run) => (
                  <li key={run.id}>
                    <Link className="line-row group" to={`/runs/${run.id}`}>
                      <span className="min-w-0">
                        <span className="block truncate text-[12px] text-ink group-hover:text-accent">
                          {run.prompt || 'Untitled answer'}
                        </span>
                        <span className="block font-mono text-[9.5px] text-muted">
                          {shortDate(run.started_at)} · {ago(run.started_at)}
                        </span>
                      </span>
                      <RunStatePill status={run.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-[12px] text-muted">
                No runs yet. The first one starts above.
              </p>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
