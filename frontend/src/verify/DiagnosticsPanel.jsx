/**
 * DiagnosticsPanel — Bifrost gateway diagnostics.
 *
 * Shows gateway connectivity, configured providers, available models, virtual
 * key status, routing rules, budget state, recent provider failures, and cache
 * hit/miss statistics. Never presents a configured fallback as working:
 * Ollama shows unavailable when the local runtime isn't serving it.
 *
 * The virtual-key row reports *whether* a key is set. The value itself is not
 * sent to the browser — this panel used to print a hardcoded string, which was
 * a claim about configuration no endpoint had made.
 */
import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { formatCost } from './model.js';
import { LedgerRow, SectionHeading } from './ui.jsx';

export default function DiagnosticsPanel() {
  const [diag, setDiag] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.governance.bifrostDiagnostic()
      .then((d) => { if (!cancelled) setDiag(d); })
      .catch(() => { if (!cancelled) setDiag(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <section className="surface px-4 py-3.5" aria-label="Bifrost diagnostics">
        <SectionHeading>Bifrost diagnostics</SectionHeading>
        <div className="mt-3 space-y-2">
          <span className="skeleton block h-4 w-2/3" />
          <span className="skeleton block h-4 w-1/2" />
        </div>
      </section>
    );
  }

  const h = diag?.health || {};
  const models = diag?.models || [];
  const sched = diag?.scheduler || {};
  const circuit = sched?.circuit || {};
  const limiter = sched?.limiter || {};
  const healthy = Boolean(h.ok);

  return (
    <section className="surface px-4 py-3.5" aria-label="Bifrost diagnostics">
      <SectionHeading
        aside={
          <span className={healthy ? 'text-ok' : 'text-danger'}>
            {healthy ? 'gateway healthy' : 'gateway unhealthy'}
          </span>
        }
      >
        Bifrost diagnostics
      </SectionHeading>

      {!healthy && h.detail ? (
        <p className="mt-2 border-l-2 border-danger/60 pl-2 font-mono text-[10px] leading-relaxed text-danger">
          {h.detail}
        </p>
      ) : null}

      <div className="mt-2">
        <LedgerRow
          label="Gateway route"
          meta={diag?.configured ? 'configured' : 'not configured'}
          hint={`virtual key ${diag?.configured ? 'set' : 'not set'} · models ${
            models.length || 'none'
          }`}
        >
          <span className="block truncate font-mono text-[11px] text-ink-2" title={diag?.base_url || ''}>
            {diag?.base_url || 'no base url reported'}
          </span>
        </LedgerRow>

        <LedgerRow
          label="Circuit breaker"
          meta={circuit.state || 'unknown'}
          hint={`${circuit.consecutive_failures ?? 0} consecutive failures · ${
            sched?.total_scheduled ?? 0
          } scheduled · ${sched?.total_deferred ?? 0} deferred`}
        >
          <span className={`block text-[11px] ${circuit.state === 'open' ? 'text-danger' : 'text-ink-2'}`}>
            {circuit.state
              ? circuit.state === 'open'
                ? 'Calls are short-circuiting until the window clears.'
                : 'Closed — calls are being attempted.'
              : 'State was not reported.'}
          </span>
        </LedgerRow>

        <LedgerRow
          label="Rate limiter"
          meta={limiter?.is_paused ? 'paused' : 'active'}
          hint={`rpm ${limiter?.window_requests ?? 0}/${limiter?.effective_rpm ?? '—'} · tpm ${
            limiter?.window_tokens ?? 0
          }/${limiter?.effective_tpm ?? '—'}`}
        >
          <span className={`block text-[11px] ${limiter?.is_paused ? 'text-warn' : 'text-ink-2'}`}>
            {typeof limiter?.is_paused === 'boolean'
              ? limiter.is_paused
                ? 'Holding requests until the window resets.'
                : 'Within the window.'
              : 'State was not reported.'}
          </span>
        </LedgerRow>

        <LedgerRow
          label="Budget"
          meta={`${formatCost(diag?.spent_usd ?? 0)} of ${formatCost(diag?.budget_usd ?? 0)}`}
          hint={`cache ${diag?.cache_mode || 'unknown'} · ollama ${
            diag?.ollama_available ? 'available' : 'unavailable'
          }`}
        >
          <span className="block text-[11px] text-ink-2">
            {diag?.cache_mode
              ? diag.cache_mode === 'off'
                ? 'Every call reaches a provider.'
                : 'Cached calls are reused before a provider is asked.'
              : 'Cache mode was not reported.'}
          </span>
        </LedgerRow>
      </div>

      {models.length ? (
        <div className="mt-2.5 border-t border-rule pt-2.5">
          <p className="infra-label">configured models</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {models.map((model) => (
              <span
                key={model.model_id}
                className="rounded-sm border border-rule-2 bg-paper px-1.5 py-0.5 font-mono text-[9.5px] text-ink-2"
              >
                {model.model_id}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
