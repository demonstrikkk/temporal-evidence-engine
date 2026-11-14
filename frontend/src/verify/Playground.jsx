/**
 * Playground — a model you brought, scanned by the same engine as everything else.
 *
 * Two things this page is careful about, because both are easy to fake:
 *
 * 1. **The answer is shown as written, and so is the receipt.** Markdown is
 *    rendered as formatting, with a one-click source view that shows the exact
 *    characters the model returned — a rendered table is easier to read, and
 *    the source is what "as written" has to mean. Whatever the model did to
 *    research it — searched twice, searched never, invented a function — is
 *    printed above the scan button. An answer produced with no tools is the
 *    exact case this product exists to flag, so "no tools" is a sentence here,
 *    not an empty panel.
 * 2. **Scanning is not a shortcut.** The button submits to
 *    `POST /api/verify/runs`, the same pipeline as the intake page, with the
 *    model's own generation time. There is no lighter-weight path from this page
 *    to a verdict.
 *
 * The key lives in component state for the length of the session. It is never
 * written to storage, never put in a URL, and masked on screen — this page is
 * going to be on a projector.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import Prose from '../components/Prose.jsx';
import { ErrorNote } from '../components/ui.jsx';
import { shortDate, shortUrl, timeOnly } from './model.js';
import { formatDuration } from './traceModel.js';
import {
  costLine,
  endpointLabel,
  endpointHost,
  errorMessage,
  errorReason,
  keySuggestion,
  maskKey,
  resultRoute,
  scanPayload,
  scanReadiness,
  toolOutcome,
  toolQuery,
  toolSummary,
  usageLine,
} from './playgroundModel.js';
import { Empty, Field, SectionHeading, SkeletonBlock } from './ui.jsx';

const PROMPTS = [
  'What does ChatGPT Plus cost today, and what model does it use?',
  'Which Python version is the current stable release?',
  'How large is the context window of the latest Claude model?',
];

function PresetChip({ preset, active, onSelect }) {
  return (
    <button
      type="button"
      className={`pg-preset${active ? ' is-on' : ''}`}
      aria-pressed={active}
      onClick={() => onSelect(preset)}
      title={preset.note || preset.base_url || preset.label}
    >
      {preset.label}
      {preset.source === 'gateway' ? (
        <span className="pg-preset-tag">keyless</span>
      ) : preset.local ? (
        <span className="pg-preset-tag">local</span>
      ) : null}
    </button>
  );
}

function Step({ call, index }) {
  const outcome = toolOutcome(call);
  const query = toolQuery(call);
  const urls = Array.isArray(call.urls) ? call.urls : [];
  return (
    <li className="pg-step" data-tone={outcome.tone}>
      <span className="pg-step-num">{index + 1}</span>
      <div className="min-w-0">
        <div className="pg-step-head">
          <span className="pg-step-name">{call.name || 'tool'}</span>
          <span className="pg-step-outcome">{outcome.label}</span>
          <span className="pg-step-lat">{formatDuration(call.latency_ms)}</span>
        </div>
        {query ? <p className="pg-step-query">{query}</p> : null}
        {urls.length ? (
          <ul className="pg-step-urls">
            {urls.slice(0, 3).map((url) => (
              <li key={url}>
                <a href={url} target="_blank" rel="noreferrer noopener">
                  {shortUrl(url, 42)}
                </a>
              </li>
            ))}
            {urls.length > 3 ? <li className="text-muted">+{urls.length - 3} more</li> : null}
          </ul>
        ) : null}
      </div>
    </li>
  );
}

export default function Playground() {
  const navigate = useNavigate();
  const [presets, setPresets] = useState([]);
  const [toolsAvailable, setToolsAvailable] = useState(false);
  const [presetId, setPresetId] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [revealKey, setRevealKey] = useState(false);
  const [useTools, setUseTools] = useState(true);
  const [question, setQuestion] = useState('');
  const [thread, setThread] = useState([]);
  const [result, setResult] = useState(null);
  const [asked, setAsked] = useState('');
  const [busy, setBusy] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState(null);
  // The answer arrives as Markdown. Rendered is the readable view; source is
  // the exact characters, kept because "verbatim" is a claim this page makes.
  const [answerView, setAnswerView] = useState('formatted');
  const answerRef = useRef(null);
  const keyRef = useRef(null);

  useEffect(() => {
    let alive = true;
    api.playground
      .presets()
      .then((data) => {
        if (!alive) return;
        const list = data.presets || [];
        setPresets(list);
        setToolsAvailable(Boolean(data.tools_available));
        const first =
          list.find((item) => item.source === 'gateway') ||
          list.find((item) => item.id === 'mistral') ||
          list[0];
        if (first) {
          setPresetId(first.id);
          setBaseUrl(first.base_url);
          setModel(first.model);
        }
      })
      .catch(() => {
        if (alive) setToolsAvailable(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const preset = useMemo(
    () => presets.find((item) => item.id === presetId) || null,
    [presets, presetId],
  );
  // The deployment's own route. No key to paste, no URL to type: the gateway
  // already holds both, and asking for them again is why the first version of
  // this page answered a keyless send with a 401.
  const viaGateway = preset?.source === 'gateway';
  const readiness = scanReadiness(result);
  // A preset that needs a key is not a suggestion. Sending without one is a
  // guaranteed 401 from the provider, and the page can see that coming.
  const needsKey = !viaGateway && Boolean(preset?.key_required) && !apiKey.trim();
  const route = resultRoute(result);

  /**
   * A refused key, and the endpoint that refused it, in terms the reader can
   * act on. The backend sends `reason: auth` so this is not a string match on
   * an English sentence.
   */
  const keyRecovery = useMemo(() => {
    if (errorReason(error) !== 'auth') return null;
    const host = endpointHost(error?.payload?.error?.details?.endpoint) || endpointLabel(baseUrl);
    const hint = keySuggestion(apiKey, presets, baseUrl);
    return { host, hint };
  }, [error, apiKey, presets, baseUrl]);

  const applyPreset = (item, { keepKey = false } = {}) => {
    setPresetId(item.id);
    // The gateway has no URL of its own to show. Leaving the previous
    // endpoint's URL in the field would claim the call goes somewhere it does
    // not.
    setBaseUrl(item.source === 'gateway' ? '' : item.base_url || '');
    if (item.model) setModel(item.model);
    // The key is not carried across endpoints by default: a key that belongs to
    // one provider is worthless at another and looks like a bug when it fails.
    // A recovery switch is the exception — the key is the thing that told us
    // where to go, so throwing it away would lose the user's work.
    if (!keepKey) {
      setApiKey('');
      setRevealKey(false);
    }
  };

  const choosePreset = (item) => applyPreset(item);

  /** A preset worth switching to, when the key names one and we are elsewhere. */
  const switchTo =
    keyRecovery?.hint && !keyRecovery.hint.matchesEndpoint && keyRecovery.hint.preset
      ? keyRecovery.hint.preset
      : null;

  const recoveryBody = (() => {
    if (!keyRecovery) return '';
    const hint = keyRecovery.hint;
    if (!hint) {
      return 'Check that the key belongs to this provider and that the base URL is the right one. The two have to match.';
    }
    if (hint.family.id === 'tavily') {
      return 'That is a Tavily search key, not a model key. It is configured in this deployment’s environment; the model key field is for a chat provider.';
    }
    if (!hint.preset) {
      return `A ${hint.family.label} key cannot be used by this client. It speaks the OpenAI chat format, which ${hint.family.label} does not serve.`;
    }
    if (hint.matchesEndpoint) {
      return `The key belongs to ${keyRecovery.host}, and that endpoint refused it. It may be expired, revoked, or from a different account.`;
    }
    return `That looks like a ${hint.family.label} key, and this page is pointed at ${keyRecovery.host}.`;
  })();

  const ask = async (event) => {
    event?.preventDefault();
    const text = question.trim();
    if (!text || busy) return;
    if (needsKey) {
      setError(
        new Error(
          `${preset?.label || 'This endpoint'} needs its own API key. Keys are per-provider: ` +
            'a key from somewhere else is refused with HTTP 401.',
        ),
      );
      keyRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    const messages = [
      ...thread.map((turn) => ({ role: turn.role, content: turn.content })),
      { role: 'user', content: text },
    ];
    try {
      const out = await api.playground.chat({
        source: viaGateway ? 'gateway' : 'endpoint',
        base_url: viaGateway ? '' : baseUrl.trim(),
        model: model.trim(),
        api_key: viaGateway ? '' : apiKey,
        messages,
        temperature: 0.2,
        max_output_tokens: 900,
        allow_tools: useTools,
        max_tool_calls: 2,
      });
      setResult(out);
      setAsked(text);
      setThread([...messages, { role: 'assistant', content: out.answer }]);
      setQuestion('');
      requestAnimationFrame(() => {
        answerRef.current?.focus?.();
        answerRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      });
    } catch (err) {
      setError(err);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const scan = async () => {
    if (!readiness.ready || scanning) return;
    setScanning(true);
    setError(null);
    try {
      const created = await api.verify.createRun(scanPayload(result, asked));
      navigate(`/runs/${created.run_id}`);
    } catch (err) {
      setError(err);
      setScanning(false);
    }
  };

  const reset = () => {
    setThread([]);
    setResult(null);
    setAsked('');
    setError(null);
    setQuestion('');
    setAnswerView('formatted');
  };

  return (
    <div className="animate-fade-up space-y-6">
      <header className="page-head">
        <div>
          <p className="eyebrow">Playground</p>
          <h1 className="page-title">
            Bring a model.
            <br />
            <em>Then check what it said.</em>
          </h1>
          <p className="page-lede">
            Ask this deployment&rsquo;s own model — routed through the same gateway the verification
            uses, with the provider key kept on the server — or point the engine at any
            OpenAI-compatible endpoint with your own key, a hosted model or one running on this
            machine. Either way you see the tool calls it makes, then scan the answer: the same
            claim extraction, live research and verdicts a pasted answer gets.
          </p>
        </div>
        <p className="case-crumb hidden lg:flex">
          <span>{viaGateway ? 'key held' : 'key stays'}</span>
          <span aria-hidden="true">·</span>
          <span>{viaGateway ? 'server-side' : 'in this browser tab'}</span>
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
        <form className="intake-panel self-start" onSubmit={ask} aria-label="Playground instrument">
          <div className="intake-bar">
            <span className="intake-bar-label">the instrument</span>
            <span className="intake-metrics">
              <span>
                <b>{thread.length || 0}</b> turns
              </span>
            </span>
          </div>

          <div className="space-y-4 p-4">
            <div>
              <p className="label">Endpoint</p>
              <div className="pg-presets">
                {presets.map((item) => (
                  <PresetChip
                    key={item.id}
                    preset={item}
                    active={item.id === presetId}
                    onSelect={choosePreset}
                  />
                ))}
              </div>
              {viaGateway ? (
                <p className="pg-route">
                  <span className="pg-route-label">route</span>
                  <span className="pg-route-value">
                    gateway &middot; provider key server-side
                  </span>
                </p>
              ) : (
                <>
                  <input
                    className="input mt-2 font-mono text-[11.5px]"
                    value={baseUrl}
                    onChange={(event) => {
                      setBaseUrl(event.target.value);
                      setPresetId('custom');
                    }}
                    placeholder="https://api.example.com/v1"
                    spellCheck={false}
                    aria-label="Base URL"
                    required
                  />
                  <p className="mt-1 text-[10.5px] leading-snug text-muted">
                    Must be OpenAI-compatible. Public endpoints need{' '}
                    <span className="font-mono">https</span>; a local server is limited to the
                    ports Ollama, LM Studio, llama.cpp and vLLM use.
                  </p>
                </>
              )}
            </div>

            <Field
              label="Model"
              hint={
                viaGateway
                  ? 'The gateway routes provider/model. Change it to ask for another model it serves.'
                  : 'The id the endpoint expects. Not every provider uses the same name.'
              }
            >
              <input
                className="input font-mono text-[12px]"
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder={viaGateway ? 'mistral/mistral-small-latest' : 'mistral-small-latest'}
                spellCheck={false}
                required
              />
            </Field>

            {!viaGateway ? (
              <div>
                <div className="flex items-baseline justify-between gap-2">
                  <p className="label mb-0">API key</p>
                <button
                  type="button"
                  className="font-mono text-[9.5px] uppercase tracking-[0.1em] text-muted underline decoration-rule-2 underline-offset-2 hover:text-ink-2"
                  onClick={() => setRevealKey((value) => !value)}
                  aria-pressed={revealKey}
                >
                  {revealKey ? 'hide' : 'show'}
                </button>
              </div>
              <input
                ref={keyRef}
                className="input mt-1.5 font-mono text-[11.5px]"
                type={revealKey ? 'text' : 'password'}
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={preset?.key_required ? 'paste your key' : 'not needed locally'}
                autoComplete="off"
                spellCheck={false}
                aria-label="API key"
                aria-required={Boolean(preset?.key_required)}
              />
              <p className="mt-1 flex flex-wrap items-baseline gap-x-2 font-mono text-[10px] text-muted">
                <span>{maskKey(apiKey)}</span>
                {apiKey ? (
                  <button
                    type="button"
                    className="underline decoration-rule-2 underline-offset-2 hover:text-ink-2"
                    onClick={() => {
                      setApiKey('');
                      setRevealKey(false);
                    }}
                  >
                    forget
                  </button>
                ) : null}
                {preset?.key_url ? (
                  <a
                    className="underline decoration-rule-2 underline-offset-2 hover:text-ink-2"
                    href={preset.key_url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    get one
                  </a>
                ) : null}
              </p>
              {needsKey ? (
                <p className="pg-key-note">
                  {preset.label} refuses a request without its own key — there is no anonymous
                  tier to fall back on.
                </p>
              ) : null}
            </div>
            ) : null}

            <label className="flex items-start gap-2 text-[12px] leading-snug text-ink-2">
              <input
                type="checkbox"
                className="mt-0.5 h-3.5 w-3.5 accent-[rgb(var(--accent))]"
                checked={useTools}
                disabled={!toolsAvailable}
                onChange={(event) => setUseTools(event.target.checked)}
              />
              <span>
                Let the model search the web
                <span className="mt-0.5 block text-[10.5px] text-muted">
                  {toolsAvailable
                    ? 'Offers one tool, web_search. Every call it makes is listed below.'
                    : 'Unavailable: this deployment has no Tavily key, so there is nothing to call.'}
                </span>
              </span>
            </label>

            <div className="pg-instrument-foot">
              <button
                type="submit"
                className="btn-accent w-full"
                disabled={
                  busy ||
                  needsKey ||
                  !question.trim() ||
                  (!viaGateway && !baseUrl.trim()) ||
                  !model.trim()
                }
                title={needsKey ? `${preset.label} needs its API key first` : undefined}
              >
                {busy ? 'Asking.' : 'Ask the model'}
              </button>
              {thread.length ? (
                <button type="button" className="btn-ghost w-full" onClick={reset}>
                  Start a new conversation
                </button>
              ) : null}
              <p className="text-[10px] leading-relaxed text-muted">
                {viaGateway
                  ? 'This call goes through Bifrost — the gateway the verification uses. The provider key stays on the server; the receipt below reports who actually answered.'
                  : 'This call bypasses Bifrost: the endpoint and the key are yours, so there is nothing for the gateway to route. Scanning the answer starts a normal run, and that does run through it.'}
              </p>
            </div>
          </div>
        </form>

        <div className="min-w-0 space-y-4">
          <form className="pg-ask" onSubmit={ask}>
            <label className="sr-only" htmlFor="pg-question">
              Your question
            </label>
            <textarea
              id="pg-question"
              className="pg-ask-input"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) ask(event);
              }}
              placeholder="Ask something with a fact that can go stale — a price, a version, a person in a role."
              rows={2}
              maxLength={4000}
            />
            <div className="pg-ask-foot">
              <span className="pg-ask-hint">⌘/Ctrl + Enter to send</span>
              <button type="submit" className="btn-outline btn-xs" disabled={busy || !question.trim()}>
                Send
              </button>
            </div>
            {!thread.length ? (
              <div className="pg-ask-prompts">
                {PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    className="pg-prompt"
                    onClick={() => setQuestion(prompt)}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            ) : null}
          </form>

          <ErrorNote
            error={error ? { ...error, message: errorMessage(error) } : null}
            title="That did not work"
          />

          {keyRecovery ? (
            <div className="pg-recover" role="note">
              <p className="pg-recover-title">
                Refused by <span className="font-mono">{keyRecovery.host}</span>
              </p>
              <p className="pg-recover-body">{recoveryBody}</p>
              <div className="pg-recover-actions">
                {switchTo ? (
                  <button
                    type="button"
                    className="btn-outline btn-xs"
                    onClick={() => {
                      applyPreset(switchTo, { keepKey: true });
                      setError(null);
                    }}
                  >
                    Use {switchTo.label} instead
                  </button>
                ) : null}
                {preset?.key_url ? (
                  <a
                    className="pg-recover-link"
                    href={preset.key_url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    Get a {preset.label} key
                  </a>
                ) : null}
              </div>
            </div>
          ) : null}

          {busy ? (
            <section className="surface p-4" aria-live="polite">
              <SectionHeading aside="waiting">The model is answering</SectionHeading>
              <div className="mt-3">
                <SkeletonBlock lines={4} />
              </div>
            </section>
          ) : null}

          {!busy && !result && !error ? (
            <Empty title="Nothing asked yet">
              The gateway is preselected, so there is no key to paste. Ask a question and the answer
              appears here with the tool calls behind it, and a button that sends it through
              verification. Switch to any other endpoint to bring your own model.
            </Empty>
          ) : null}

          {result ? (
            <>
              <section className="pg-sheet" aria-label="Model answer">
                <div className="pg-sheet-head">
                  <span className="pg-sheet-label">answer · as written</span>
                  <span className="pg-sheet-meta">
                    {route.model} · {route.where}
                  </span>
                  <span className="view-switch" role="group" aria-label="Answer view">
                    <button
                      type="button"
                      aria-pressed={answerView === 'formatted'}
                      onClick={() => setAnswerView('formatted')}
                      title="The model's words, interpreted as Markdown"
                    >
                      formatted
                    </button>
                    <button
                      type="button"
                      aria-pressed={answerView === 'source'}
                      onClick={() => setAnswerView('source')}
                      title="The exact characters the model returned, before any interpretation"
                    >
                      source
                    </button>
                  </span>
                </div>
                {asked ? (
                  <p className="pg-asked">
                    <span className="pg-asked-label">you asked</span>
                    <span className="pg-asked-text">{asked}</span>
                  </p>
                ) : null}
                {answerView === 'source' ? (
                  <pre className="pg-answer pg-raw" ref={answerRef} tabIndex={-1}>
                    {result.answer ||
                      'The model ended its turn with a tool call and no prose. Ask again — there is nothing to scan here.'}
                  </pre>
                ) : (
                  <div className="pg-answer pg-md" ref={answerRef} tabIndex={-1}>
                    {result.answer ? (
                      <Prose>{result.answer}</Prose>
                    ) : (
                      <p className="pg-md-empty">
                        The model ended its turn with a tool call and no prose. Ask again — there is
                        nothing to scan here.
                      </p>
                    )}
                  </div>
                )}
                <p className="pg-sheet-foot">
                  Produced{' '}
                  {result.generated_at
                    ? `${shortDate(result.generated_at)} at ${timeOnly(result.generated_at)}`
                    : 'at an unrecorded time'}
                  . Markdown is interpreted as formatting; the source view shows the exact
                  characters returned. Nothing on this card has been rewritten.
                </p>
              </section>

              <section className="surface p-4" aria-label="Model receipt">
                <SectionHeading aside={`${result.rounds || 1} round${result.rounds === 1 ? '' : 's'}`}>
                  What the model did
                </SectionHeading>

                <dl className="pg-kv">
                  <div>
                    <dt>model</dt>
                    <dd>{route.model}</dd>
                  </div>
                  {route.gateway ? (
                    <>
                      <div>
                        <dt>provider</dt>
                        <dd>{result.provider || 'not reported'}</dd>
                      </div>
                      <div>
                        <dt>route</dt>
                        <dd>{endpointLabel(result.endpoint)} · gateway</dd>
                      </div>
                      <div>
                        <dt>cost</dt>
                        <dd>{costLine(result.cost_usd)}</dd>
                      </div>
                      {result.request_id ? (
                        <div>
                          <dt>request</dt>
                          <dd className="break-all">{result.request_id}</dd>
                        </div>
                      ) : null}
                    </>
                  ) : (
                    <div>
                      <dt>endpoint</dt>
                      <dd>{route.where}</dd>
                    </div>
                  )}
                  <div>
                    <dt>wall clock</dt>
                    <dd>{formatDuration(result.latency_ms)}</dd>
                  </div>
                  <div>
                    <dt>tokens</dt>
                    <dd>{usageLine(result.usage)}</dd>
                  </div>
                  <div>
                    <dt>finish</dt>
                    <dd>{result.finish_reason || 'not reported'}</dd>
                  </div>
                </dl>

                <div className="pg-tools">
                  <p className="pg-tools-head">
                    {result.tools_enabled ? 'Tool ledger' : 'No tools were offered'}
                    <span className="pg-tools-line">{toolSummary(result)}</span>
                  </p>
                  {result.tool_calls?.length ? (
                    <ol className="pg-steps">
                      {result.tool_calls.map((call, index) => (
                        <Step key={`${call.id || call.name}-${index}`} call={call} index={index} />
                      ))}
                    </ol>
                  ) : (
                    <p className="pg-tools-empty">
                      {result.tools_enabled
                        ? 'The model answered from what it already knew. That is exactly the condition a stale answer comes from.'
                        : 'This answer was written with no live sources. Scanning it is the point.'}
                    </p>
                  )}
                </div>
              </section>

              <section className="pg-scan" aria-label="Scan this answer">
                <div className="min-w-0">
                  <p className="pg-scan-title">Scan this answer</p>
                  <p className="pg-scan-note">
                    Extraction, live research, and one verdict per claim — the same run a pasted
                    answer gets, using the generation time above.
                  </p>
                </div>
                <button
                  type="button"
                  className="btn-accent shrink-0"
                  onClick={scan}
                  disabled={!readiness.ready || scanning}
                  title={readiness.reason || 'Start a verification run'}
                >
                  {scanning ? 'Starting.' : 'Verify with TEV'}
                  {scanning ? null : <span aria-hidden="true">→</span>}
                </button>
              </section>

              {thread.length > 1 ? (
                <p className="text-[10.5px] text-muted">
                  This conversation is {thread.length} messages deep. The scan covers the answer
                  above and the question that produced it, not the turns before it.{' '}
                  <button
                    type="button"
                    className="underline decoration-rule-2 underline-offset-2 hover:text-ink-2"
                    onClick={reset}
                  >
                    Start over
                  </button>
                </p>
              ) : null}
            </>
          ) : null}

          <p className="pg-disclaimer">
            Prefer to paste an answer you already have?{' '}
            <Link className="link" to="/check">
              The intake page
            </Link>{' '}
            takes text and a generation time.
          </p>
        </div>
      </div>
    </div>
  );
}
