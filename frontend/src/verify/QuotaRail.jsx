/**
 * QuotaRail — infrastructure panel showing quota state.
 *
 * Displays Tavily credits, Groq token/request state, Bifrost provider info,
 * cache hit rate, and saved calls. Never invents precise remaining quota when
 * the provider has not supplied it; marks estimates as estimates.
 *
 * Rendered as a compact ledger, not a card grid, and placed *after* the
 * evidence: quota is context for the operator, not the reason to open a run.
 */
import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { formatCost, countLabel } from './model.js';
import { SectionHeading, LedgerRow } from './ui.jsx';

function Bar({ value, tone = 'bg-ink' }) {
  return (
    <span className="infra-bar" aria-hidden="true">
      <span className={tone} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </span>
  );
}

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

  // The diagnostic returns provider objects; joining them directly printed
  // "[object Object]" in the footer. Name them, and say nothing when unnamed.
  const providerNames = providers
    .map((provider) => (typeof provider === 'string' ? provider : provider?.name || provider?.id))
    .filter(Boolean);

  if (loading) {
    return (
      <section className="surface px-4 py-3.5" aria-label="Quota and infrastructure">
        <SectionHeading>Quota &amp; infrastructure</SectionHeading>
        <div className="mt-3 space-y-2">
          <span className="skeleton block h-4 w-full" />
          <span className="skeleton block h-4 w-2/3" />
          <span className="skeleton block h-4 w-1/2" />
        </div>
      </section>
    );
  }

  return (
    <section className="surface px-4 py-3.5" aria-label="Quota and infrastructure">
      <SectionHeading aside={mode === 'offline' ? <span className="text-danger">{mode}</span> : mode}>
        Quota &amp; infrastructure
      </SectionHeading>

      <div className="mt-2">
        <LedgerRow
          label="Tavily credits"
          meta={`${tRemaining} of ${tLimit}`}
          hint={`${tUsed} used · ${t.session_searches ?? 0} session searches`}
        >
          <Bar value={(tUsed / tLimit) * 100} tone="bg-ember" />
        </LedgerRow>
        <LedgerRow
          label="Groq · daily"
          meta={`${countLabel(gRequests)} req`}
          hint={`${formatCost(spent)} spent · ${formatCost(budget - spent)} of ${formatCost(
            budget
          )} budget`}
        >
          <Bar value={(gUsed / 500000) * 100} tone="bg-ok" />
        </LedgerRow>
        <LedgerRow
          label="Cache hit rate"
          meta={`${Math.round(hitRate * 100)}%`}
          hint={`${Number(cache?.external_calls_saved?.total || 0)} external calls saved · ${Number(
            cache?.saved_model_calls || 0
          )} model · ${Number(cache?.saved_tavily_searches || 0)} search`}
        >
          <Bar value={hitRate * 100} tone="bg-ok" />
        </LedgerRow>
      </div>

      <p className="mt-2.5 flex flex-wrap items-center justify-between gap-2 border-t border-rule pt-2 font-mono text-[9.5px] text-muted">
        <span title={providerNames.join(', ')}>
          {providerNames.length
            ? `providers · ${providerNames.join(', ')}`
            : 'providers · not reported by the gateway'}
        </span>
        <span>cache mode · {cache?.raw?.cache_mode || 'unknown'}</span>
      </p>
    </section>
  );
}
