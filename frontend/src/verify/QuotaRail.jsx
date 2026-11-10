/**
 * QuotaRail — infrastructure panel showing quota state.
 *
 * Displays Tavily credits, Groq token/request state, Bifrost provider info,
 * cache hit rate, and saved calls. Never invents precise remaining quota when
 * the provider has not supplied it; marks estimates as estimates.
 */
import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { formatCost, countLabel } from './model.js';

export default function QuotaRail() {
  const [quota, setQuota] = useState(null);
  const [cache, setCache] = useState(null);
  const [diagnostic, setDiagnostic] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.governance.quota().then((d) => { if (!cancelled) setQuota(d); }),
      api.governance.cacheMetrics().then((d) => { if (!cancelled) setCache(d); }),
      api.governance.bifrostDiagnostic().then((d) => { if (!cancelled) setDiagnostic(d); }),
    ]).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="surface p-3">
        <div className="h-5 w-32 skeleton mb-1" />
        <div className="h-4 w-24 skeleton" />
      </div>
    );
  }

  const t = quota?.quota?.tavily || {};
  const g = quota?.quota?.groq || {};
  const c = cache?.layer_counts || {};
  const mode = quota?.current_budget_mode || 'balanced';

  const tUsed = Number(t.credits_used || 0);
  const tLimit = Number(t.monthly_limit || 1000);
  const tRemaining = Number(t.credits_remaining || Math.max(0, tLimit - tUsed));
  const gUsed = Number(g.daily_tokens_used || 0);
  const gRequests = Number(g.session_requests || 0);
  const hitRate = Number(cache?.overall_hit_rate || 0);

  const providers = diagnostic?.health?.providers || [];
  const budget = diagnostic?.budget_usd != null ? diagnostic.budget_usd : 0;
  const spent = diagnostic?.spent_usd || 0;

  return (
    <div className="surface p-4 space-y-3" aria-label="Quota and infrastructure">
      <div className="flex items-center justify-between">
        <span className="label text-[11px] text-muted uppercase tracking-wider">Quota &amp; infrastructure</span>
        <span className={`font-mono text-[10px] ${mode === 'offline' ? 'text-danger' : 'text-muted'}`}>
          {mode}
        </span>
      </div>

      <div className="grid gap-2">
        {/* Tavily */}
        <div className="border border-rule/30 rounded-md p-2.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-ink-2">Tavily credits</span>
            <span className="font-mono text-ink">{tRemaining} / {tLimit}</span>
          </div>
          <div className="h-1.5 w-full bg-paper-2 rounded-full mt-1.5 overflow-hidden">
            <div
              className="h-full bg-info"
              style={{ width: `${Math.min(100, (tUsed / tLimit) * 100)}%` }}
            />
          </div>
          <div className="flex justify-between text-[9px] text-muted mt-0.5">
            <span>{tUsed} used</span>
            <span>{tRemaining} remaining</span>
            <span>{t.session_searches ?? 0} session searches</span>
          </div>
        </div>

        {/* Groq */}
        <div className="border border-rule/30 rounded-md p-2.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-ink-2">Groq (daily)</span>
            <span className="font-mono text-ink">{countLabel(gRequests)} req</span>
          </div>
          <div className="h-1.5 w-full bg-paper-2 rounded-full mt-1.5 overflow-hidden">
            <div className="h-full bg-ok" style={{ width: `${Math.min(100, (gUsed / 500000) * 100)}%` }} />
          </div>
          <div className="flex justify-between text-[9px] text-muted mt-0.5">
            <span>{formatCost(spent)} spent</span>
            <span>{formatCost(budget - spent)} of ${budget.toFixed(2)} budget</span>
          </div>
        </div>

        {/* Cache */}
        <div className="border border-rule/30 rounded-md p-2.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-ink-2">Cache hit rate</span>
            <span className="font-mono text-ink">{Math.round(hitRate * 100)}%</span>
          </div>
          <div className="h-1.5 w-full bg-paper-2 rounded-full mt-1.5 overflow-hidden">
            <div
              className="h-full bg-ok"
              style={{ width: `${Math.min(100, hitRate * 100)}%` }}
            />
          </div>
          <div className="flex justify-between text-[9px] text-muted mt-0.5">
            <span>{Number(cache?.external_calls_saved?.total || 0)} external calls saved</span>
            <span>{Number(cache?.saved_model_calls || 0)} model</span>
            <span>{Number(cache?.saved_tavily_searches || 0)} search</span>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between text-[9px] text-muted">
        <span>Providers: {providers.join(', ') || '—'}</span>
        <span>Cache mode: {cache?.raw?.cache_mode || '—'}</span>
      </div>
    </div>
  );
}
