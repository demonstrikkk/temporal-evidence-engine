/**
 * Intake. The only thing the product asks for is an answer and when it was
 * generated — everything else is derived.
 *
 * "When it was generated" is not optional decoration: `changed` is defined as a
 * settled replacement whose event date is after generation. Without a stated
 * generation time the engine can still find contradictions, but it refuses to
 * call anything stale, and says so.
 */
import { useEffect, useState } from 'react';
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

export default function NewCheck() {
  const navigate = useNavigate();
  const [answer, setAnswer] = useState('');
  const [prompt, setPrompt] = useState('');
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
    <div className="animate-fade-up">
      <div className="mb-6 max-w-3xl">
        <p className="eyebrow">Claim-level verification</p>
        <h1 className="mt-2 font-display text-[34px] leading-[1.08] text-ink sm:text-[40px]">
          An answer is a set of claims.
          <br />
          <span className="text-accent">Check each one.</span>
        </h1>
        <p className="mt-3 max-w-2xl text-[13.5px] leading-relaxed text-ink-2">
          Paste an AI-generated answer and the time it was produced. The engine splits it into
          atomic claims, researches them live, and settles every quote against the exact stored
          source text — with the event date separated from the publication date, so a new article
          is never mistaken for a new fact.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
        <form onSubmit={submit} className="surface space-y-5 p-5" aria-label="New verification">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <SectionHeading>The answer</SectionHeading>
            <div className="flex flex-wrap gap-1.5">
              {EXAMPLES.map((example) => (
                <button
                  key={example.id}
                  type="button"
                  className="btn-outline btn-xs"
                  onClick={() => loadExample(example)}
                >
                  {example.label}
                </button>
              ))}
            </div>
          </div>

          <Field
            label="Answer text"
            hint="One claim per sentence works best. Opinions and predictions are ignored, not failed."
          >
            <textarea
              className="input min-h-[210px] resize-y font-mono text-[12.5px] leading-relaxed"
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              placeholder="Paste the answer here…"
              spellCheck={false}
              required
            />
          </Field>

          <Field label="Original question (context only)" hint="Optional. It helps extraction, and it is shown with the report.">
            <input
              className="input"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="What did you ask the model?"
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto]">
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
            <label className="flex items-end gap-2 pb-1 text-[12px] text-ink-2">
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

          <div className="flex flex-wrap items-center gap-3 border-t border-rule pt-4">
            <button type="submit" className="btn-primary" disabled={submitting || !answer.trim()}>
              {submitting ? 'Starting…' : 'Verify this answer'}
            </button>
            <span className="text-[11px] text-muted">
              Live research takes tens of seconds. The run reports every stage as it happens.
            </span>
          </div>
        </form>

        <aside className="space-y-5">
          <section className="surface p-5">
            <SectionHeading aside="frozen">How a verdict is decided</SectionHeading>
            <ol className="mt-3 space-y-2">
              {RULES.map(([rule, meaning, verdict]) => (
                <li key={rule} className="flex items-baseline gap-2.5 text-[12px]">
                  <span className="w-6 shrink-0 font-mono text-[10.5px] text-muted">{rule}</span>
                  <span className="min-w-0 flex-1 text-ink-2">{meaning}</span>
                  <span className="shrink-0 font-mono text-[10px] uppercase tracking-wide text-muted">
                    {verdict}
                  </span>
                </li>
              ))}
            </ol>
            <p className="mt-3 border-t border-rule pt-3 text-[11px] leading-relaxed text-muted">
              First match wins. The model proposes a relationship; a deterministic gate decides
              whether the quote establishes it; the table above decides the status from stored rows.
            </p>
          </section>

          <section className="surface p-5">
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
              <ul className="mt-3 divide-y divide-rule/70">
                {recent.map((run) => (
                  <li key={run.id}>
                    <Link
                      className="row flex items-center justify-between gap-3 py-2"
                      to={`/runs/${run.id}`}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-[12px] text-ink">
                          {run.prompt || 'Untitled answer'}
                        </span>
                        <span className="block text-[10.5px] text-muted">
                          {shortDate(run.started_at)} · {ago(run.started_at)}
                        </span>
                      </span>
                      <RunStatePill status={run.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-[12px] text-muted">No runs yet. The first one starts above.</p>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
