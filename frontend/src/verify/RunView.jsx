/**
 * One run, three panes: the answer, the claim register, the evidence.
 *
 * The layout is the argument. The answer shows *where* each claim is; the
 * register shows *what* was decided; the inspector shows *why*. Selecting a
 * claim in any pane selects it in all three, and the selection lives in the
 * URL so a reviewer can link to one verdict.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import Prose from '../components/Prose.jsx';
import { isLive, runCounts, shortDate, timeOnly } from './model.js';
import { useVerifyRun } from './useVerifyRun';
import AnswerSpans from './AnswerSpans.jsx';
import ClaimInspector from './ClaimInspector.jsx';
import ClaimRegister, { VerdictBar } from './ClaimRegister.jsx';
import EventRail from './EventRail.jsx';
import GatewayPanel from './GatewayPanel.jsx';
import { Empty, RunStatePill, SectionHeading, SkeletonBlock } from './ui.jsx';
import QuotaRail from './QuotaRail.jsx';
import BudgetModeSelector from './BudgetModeSelector.jsx';
import DiagnosticsPanel from './DiagnosticsPanel.jsx';
import CoveragePanel from './CoveragePanel.jsx';
import ResearchLedger from './ResearchLedger.jsx';
import TracePanel from './TracePanel.jsx';

const STAGE_COPY = {
  queued: 'Queued',
  extract: 'Extracting atomic claims',
  research: 'Researching and settling claims',
  done: 'Finishing',
};

function PartialBanner({ run, events }) {
  const { failures, headline, terminalError } = useMemo(() => {
    const reported = events
      .filter((event) => event.event_type === 'run.failure')
      .map((event) => event.payload?.message || 'a stage failed');
    const terminal = [...events].reverse().find((event) => event.event_type === 'run.partial');
    const error = terminal?.payload?.error || null;
    const fromFailures = (terminal?.payload?.failures || []).map((failure) => failure.message);
    const failures = reported.length ? reported : [
      ...(error?.message && error.code !== 'no_claims' ? [error.message] : []),
      ...fromFailures,
    ];
    // A run that found nothing to check is not a run that lost sources; saying
    // "the verdicts reflect only what was retrieved" there would describe
    // verdicts that do not exist.
    const headline = error?.code === 'no_claims'
      ? 'This answer contained no checkable factual claims, so there is nothing to verify.'
      : 'This run finished partially. The verdicts reflect only what was retrieved.';
    return { failures: [...new Set(failures)], headline, terminalError: error };
  }, [events]);

  if (run.status === 'failed') {
    return (
      <div role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3.5 py-2.5">
        <p className="text-[12.5px] font-medium text-danger">
          The run failed before it could produce verdicts.
        </p>
        <p className="mt-0.5 text-[11.5px] text-ink-2">
          {run.error?.message || 'No error detail was recorded.'}
          {run.error?.code ? (
            <span className="ml-1.5 font-mono text-[10.5px] text-muted">({run.error.code})</span>
          ) : null}
        </p>
      </div>
    );
  }

  if (run.status !== 'partial') return null;

  return (
    <div role="status" className="rounded-md border border-warn/35 bg-warn-soft px-3.5 py-2.5">
      <p className="text-[12.5px] font-medium text-warn">{headline}</p>
      {failures.length ? (
        <ul className="mt-1 list-inside list-disc text-[11.5px] text-ink-2">
          {failures.slice(0, 4).map((message, index) => (
            <li key={index}>{message}</li>
          ))}
        </ul>
      ) : null}
      {terminalError?.code ? (
        <p className="mt-1 font-mono text-[10.5px] text-muted">{terminalError.code}</p>
      ) : null}
    </div>
  );
}

export default function RunView() {
  const { runId } = useParams();
  const [params, setParams] = useSearchParams();
  const { run, events, error, loading, streamState } = useVerifyRun(runId);
  const [exporting, setExporting] = useState('');
  const [exportError, setExportError] = useState(null);
  /*
   * Two ways to see the same stored text.
   *
   * `formatted` reads the answer as the model wrote it — headings, tables,
   * bold — and draws every claim mark on the rendered words. `source` shows the
   * exact stored characters, Markdown syntax and all, which is what the offsets
   * literally address and what a reviewer quotes when the wording matters.
   * Neither view rewrites the stored string; the register below is
   * authoritative in both.
   */
  const [answerView, setAnswerView] = useState('formatted');

  const selectedId = params.get('claim') || '';
  const claims = run?.claims || [];
  const counts = useMemo(
    () => runCounts(claims, run?.counters),
    [run?.counters, claims],
  );

  const selectClaim = (claimId) => {
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (claimId) next.set('claim', claimId);
        else next.delete('claim');
        return next;
      },
      { replace: true },
    );
  };

  // Open the first claim once, so the inspector is never a blank pane. Later
  // selections are left alone, including a deliberate deselection.
  useEffect(() => {
    if (!selectedId && claims.length) selectClaim(claims[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claims.length, selectedId]);

  const exportReport = async (format) => {
    setExporting(format);
    setExportError(null);
    try {
      const report = await api.verify.report(runId);
      const text = format === 'md' ? report.markdown : JSON.stringify(report, null, 2);
      const blob = new Blob([text], {
        type: format === 'md' ? 'text/markdown;charset=utf-8' : 'application/json',
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `verification-${String(runId).slice(0, 8)}.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err);
    } finally {
      setExporting('');
    }
  };

  if (loading && !run) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-8 w-64" />
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_420px]">
          <div className="skeleton h-64" />
          <div className="skeleton h-96" />
        </div>
      </div>
    );
  }

  if (error && !run) {
    return (
      <div className="surface p-6" role="alert">
        <h1 className="font-display text-[22px] text-ink">This run could not be loaded</h1>
        <p className="mt-1.5 text-[12.5px] text-muted">{error.message || String(error)}</p>
        <Link className="btn-outline mt-4" to="/runs">
          Back to runs
        </Link>
      </div>
    );
  }

  if (!run) return null;

  const live = isLive(run.status);
  const answer = run.answer || {};

  return (
    <div className="space-y-5 animate-fade-up">
      <header className="case-head">
        <div className="min-w-0 flex-1">
          <div className="case-crumb">
            <Link to="/runs">runs</Link>
            <span aria-hidden="true">/</span>
            <span>{String(run.id).slice(0, 8)}</span>
            <span aria-hidden="true">·</span>
            <span>{run.status}</span>
          </div>
          <h1 className="case-title">{answer.prompt || 'Untitled answer'}</h1>
          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <RunStatePill status={run.status} />
            {answer.answer_text ? (
              <span className="font-mono text-[10px] text-muted">
                {answer.answer_text.length} chars · {claims.length} claim
                {claims.length === 1 ? '' : 's'}
              </span>
            ) : null}
          </div>
          <dl className="case-meta">
            <div>
              <dt>answer generated</dt>
              <dd className={answer.generated_at ? '' : 'is-missing'}>
                {answer.generated_at
                  ? `${shortDate(answer.generated_at)} ${timeOnly(answer.generated_at)}`
                  : 'unknown'}
              </dd>
            </div>
            <div>
              <dt>run started</dt>
              <dd>{shortDate(run.started_at)}</dd>
            </div>
            <div>
              <dt>rule</dt>
              <dd>{run.rule_version || '—'}</dd>
            </div>
            <div>
              <dt>cache</dt>
              <dd>{run.cache_mode || '—'}</dd>
            </div>
          </dl>
        </div>

        <div className="w-full shrink-0 lg:w-[318px]">
          <p className="case-sub">
            Verdict distribution
            <b>
              {counts.total ? `${counts.total - counts.pending}/${counts.total} settled` : 'no claims'}
            </b>
          </p>
          <div className="mt-2">
            <VerdictBar counts={counts} />
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <BudgetModeSelector mode={run?.budget_mode || 'balanced'} />
            <div className="flex flex-wrap justify-end gap-1.5">
              <button
                type="button"
                className="btn-outline btn-xs"
                onClick={() => exportReport('md')}
                disabled={Boolean(exporting) || live}
                title={live ? 'The report is complete when the run is' : 'Download the Markdown report'}
              >
                {exporting === 'md' ? 'Preparing…' : 'Report .md'}
              </button>
              <button
                type="button"
                className="btn-outline btn-xs"
                onClick={() => exportReport('json')}
                disabled={Boolean(exporting) || live}
                title={live ? 'The report is complete when the run is' : 'Download the JSON evidence pack'}
              >
                {exporting === 'json' ? 'Preparing…' : 'JSON'}
              </button>
            </div>
          </div>
          {exportError ? (
            <p className="mt-1.5 text-[10.5px] text-danger" role="alert">
              {exportError.message || String(exportError)}
            </p>
          ) : null}
        </div>
      </header>

      <PartialBanner run={run} events={events} />

      {live ? (
        <div className="flex items-center gap-3 rounded-md border border-rule bg-paper-2 px-3.5 py-2.5">
          <span className="relative flex h-2 w-2" aria-hidden="true">
            <span className="absolute inline-flex h-full w-full animate-pulse-ring rounded-full bg-accent" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
          </span>
          <span className="text-[12px] text-ink-2">
            {STAGE_COPY[run.stage] || run.stage || 'Working'}
            {claims.length ? ` · ${claims.length} claims extracted` : ''}
          </span>
          <span className="ml-auto font-mono text-[10.5px] text-muted">
            {counts.total ? `${counts.total - counts.pending}/${counts.total} settled` : ''}
          </span>
        </div>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_420px]">
        <div className="min-w-0 space-y-5">
          <section className="surface px-4 py-3.5" aria-label="The answer">
            <SectionHeading
              aside={`${answer.answer_text?.length || 0} chars · ${claims.length} claims`}
            >
              The answer
            </SectionHeading>
            <div className="answer-sheet">
              {answer.answer_text ? (
                <>
                  <p className="answer-caption">
                    <span>as generated</span>
                    <span className="answer-caption-right">
                      <span>
                        {answer.generated_at
                          ? `${shortDate(answer.generated_at)} ${timeOnly(answer.generated_at)}`
                          : 'generation time unknown'}
                      </span>
                      <span className="view-switch" role="group" aria-label="Answer view">
                        <button
                          type="button"
                          aria-pressed={answerView === 'formatted'}
                          onClick={() => setAnswerView('formatted')}
                          title="The answer as written, with each claim marked on the rendered text"
                        >
                          formatted
                        </button>
                        <button
                          type="button"
                          aria-pressed={answerView === 'source'}
                          onClick={() => setAnswerView('source')}
                          title="The exact stored characters, before any Markdown is interpreted"
                        >
                          source
                        </button>
                      </span>
                    </span>
                  </p>
                  {answerView === 'formatted' ? (
                    <>
                      <div className="run-md">
                        <Prose marks={{ claims, selectedId, onSelect: selectClaim }}>
                          {answer.answer_text}
                        </Prose>
                      </div>
                      <p className="answer-note">
                        Rendered from the stored text — nothing here was rewritten. Each mark is
                        drawn at the offset the extractor recorded; switch to <em>source</em> to see
                        the exact characters, or use the register below for every verdict.
                      </p>
                    </>
                  ) : (
                    <>
                      <AnswerSpans
                        answer={answer.answer_text}
                        claims={claims}
                        selectedId={selectedId}
                        onSelect={selectClaim}
                      />
                      <p className="answer-note">
                        The exact stored characters, so Markdown syntax is visible where the model
                        wrote it. This is the view to quote from: every span is a slice of this
                        string at the offset the extractor stored.
                      </p>
                    </>
                  )}
                </>
              ) : (
                <p className="text-[12px] text-muted">No answer text was stored for this run.</p>
              )}
            </div>
          </section>

          <CoveragePanel
            runId={runId}
            summary={run.coverage}
            answerText={answer.answer_text}
            live={live}
          />

          <section className="surface px-4 py-3.5" aria-label="Claim register">
            <SectionHeading
              aside={
                claims.length
                  ? `${counts.supported + counts.changed + counts.contradicted} decided · ${
                      counts.unverifiable
                    } abstained`
                  : null
              }
            >
              Claim register
            </SectionHeading>
            <div className="mt-2.5">
              {claims.length ? (
                <ClaimRegister claims={claims} selectedId={selectedId} onSelect={selectClaim} />
              ) : live ? (
                <div className="py-4">
                  <SkeletonBlock lines={4} />
                </div>
              ) : (
                <Empty title="No claims">
                  {run.error?.code === 'no_claims'
                    ? 'The answer contained no checkable factual claims. That is a result.'
                    : 'Extraction produced no claims for this answer.'}
                </Empty>
              )}
            </div>
          </section>

          <EventRail events={events} streamState={streamState} />

          <TracePanel runId={runId} live={live} />

          <ResearchLedger events={events} />

          <GatewayPanel gateway={run.gateway} />
        </div>

        <aside className="min-w-0">
          {/* A pinned inspector taller than the viewport would have its bottom
              cut off with no way to reach it, so it scrolls inside its frame. */}
          <div className="lg:sticky lg:top-[72px] lg:max-h-[calc(100dvh-88px)] lg:overflow-y-auto lg:overscroll-contain lg:pr-1">
            <ClaimInspector
              runId={runId}
              claimId={selectedId}
              answerGeneratedAt={answer.generated_at}
              version={claims.length}
            />
          </div>
        </aside>
      </div>

      <QuotaRail />

      <DiagnosticsPanel />
    </div>
  );
}
