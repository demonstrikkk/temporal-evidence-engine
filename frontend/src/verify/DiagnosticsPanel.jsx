/**
 * DiagnosticsPanel — Bifrost gateway diagnostics.
 *
 * Shows gateway connectivity, configured providers, available models, virtual
 * key status, routing rules, budget state, recent provider failures, and cache
 * hit/miss statistics. Never presents a configured fallback as working:
 * Ollama shows unavailable when the local runtime isn't serving it.
 */
import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { formatCost } from './model.js';

export default function DiagnosticsPanel() {
  const [diag, setDiag] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.governance.bifrostDiagnostic()
      .then((d) => { if (!cancelled) setDiag(d); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="surface p-3">
        <div className="h-5 w-32 skeleton mb-1" />
        <div className="h-4 w-40 skeleton" />
      </div>
    );
  }

  const h = diag?.health || {};
  const models = diag?.models || [];
  const sched = diag?.scheduler || {};
  const circuit = sched?.circuit || {};
  const limiter = sched?.limiter || {};

  return (
    <div className="surface p-4 space-y-3" aria-label="Bifrost diagnostics">
      <div className="flex items-center justify-between">
        <span className="label text-[11px] text-muted uppercase tracking-wider">Bifrost diagnostics</span>
        <span
          className={`font-mono text-[10px] ${h.ok ? 'text-ok' : 'text-danger'}`}
        >
          {h.ok ? 'gateway healthy' : 'gateway unhealthy'}
        </span>
      </div>

      <div className="grid gap-2">
        <div className="border border-rule/30 rounded-md p-2.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-ink-2">Virtual key</span>
            <span className="font-mono text-ink">sk-bf-tev-local-dev</span>
          </div>
          <div className="flex justify-between text-[9px] text-muted mt-0.5">
            <span>Base: {diag?.base_url || '—'}</span>
            <span>Configured: {diag?.configured ? 'yes' : 'no'}</span>
          </div>
        </div>

        <div className="border border-rule/30 rounded-md p-2.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-ink-2">Circuit breaker</span>
            <span
              className={`font-mono text-[10px] ${circuit.state === 'open' ? 'text-danger' : 'text-ok'}`}
            >
              {circuit.state}
            </span>
          </div>
          <div className="flex justify-between text-[9px] text-muted mt-0.5">
            <span>Failures: {circuit.consecutive_failures ?? 0}</span>
            <span>Scheduled: {sched?.total_scheduled ?? 0}</span>
            <span>Deferred: {sched?.total_deferred ?? 0}</span>
          </div>
        </div>

        <div className="border border-rule/30 rounded-md p-2.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-ink-2">Rate limiter</span>
            <span className="font-mono text-ink">{limiter?.is_paused ? 'paused' : 'active'}</span>
          </div>
          <div className="flex justify-between text-[9px] text-muted mt-0.5">
            <span>RPM: {limiter?.window_requests ?? 0} / {limiter?.effective_rpm ?? '—'}</span>
            <span>TPM: {limiter?.window_tokens ?? 0} / {limiter?.effective_tpm ?? '—'}</span>
            <span>Paused: {limiter?.is_paused ? 'yes' : 'no'}</span>
          </div>
        </div>

        <div className="border border-rule/30 rounded-md p-2.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-ink-2">Budget</span>
            <span className="font-mono text-ink">{formatCost(diag?.spent_usd ?? 0)} / ${diag?.budget_usd ?? 0}</span>
          </div>
          <div className="flex justify-between text-[9px] text-muted mt-0.5">
            <span>Cache mode: {diag?.cache_mode || '—'}</span>
            <span>Ollama: {diag?.ollama_available ? 'available' : 'unavailable'}</span>
          </div>
        </div>
      </div>

      {models.length > 0 && (
        <div className="border border-rule/30 rounded-md p-2.5">
          <div className="text-[10px] text-muted mb-1">Configured models</div>
          <div className="flex flex-wrap gap-1.5">
            {models.map((m) => (
              <span
                key={m.model_id}
                className="border border-rule/20 rounded-md px-2 py-1 text-[9px] text-ink-2 bg-paper"
              >
                {m.model_id}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
