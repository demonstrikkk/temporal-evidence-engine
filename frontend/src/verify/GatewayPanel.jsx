/**
 * Every model call, as the gateway recorded it.
 *
 * This panel exists because "we use Bifrost" is a claim, and a claim needs
 * evidence. The numbers here come from the same rows the backend wrote when it
 * made the call: provider actually used, model, tokens, cost, latency and how
 * many fallbacks were tried. A run with no calls says so rather than showing an
 * empty table that looks like a bug.
 */
import { countLabel, formatCost, gatewayRoute, shortDate, timeOnly } from './model.js';
import { Empty, SectionHeading } from './ui.jsx';

const STAGE_LABEL = {
  extract_claims: 'Extract claims',
  propose_relationship: 'Read source',
  replacement_value: 'Replacement value',
  plan_research: 'Plan research',
};

function Tokens({ call }) {
  const input = Number(call?.input_tokens) || 0;
  const output = Number(call?.output_tokens) || 0;
  if (!input && !output) return <span className="text-muted">—</span>;
  return (
    <span className="tabular-nums">
      {countLabel(input)}
      <span className="text-muted"> in</span> · {countLabel(output)}
      <span className="text-muted"> out</span>
    </span>
  );
}

export default function GatewayPanel({ gateway }) {
  const calls = gateway?.recent || [];
  const providers = gateway?.providers || [];
  const fallbacks = Number(gateway?.fallback_attempts) || 0;

  return (
    <section className="surface p-4" aria-label="Model calls">
      <SectionHeading
        aside={
          calls.length
            ? `${countLabel(gateway.calls)} call${gateway.calls === 1 ? '' : 's'} · ${formatCost(
                gateway.cost_usd,
              )}`
            : null
        }
      >
        Model calls through Bifrost
      </SectionHeading>

      {calls.length ? (
        <>
          <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[11px] text-muted">
            <span>
              provider
              <span className="ml-1 font-mono text-ink-2">
                {providers.length ? providers.join(', ') : 'unrecorded'}
              </span>
            </span>
            <span>
              tokens
              <span className="ml-1 font-mono tabular-nums text-ink-2">
                {countLabel((Number(gateway.input_tokens) || 0) + (Number(gateway.output_tokens) || 0))}
              </span>
            </span>
            <span>
              fallbacks
              <span
                className={`ml-1 font-mono tabular-nums ${
                  fallbacks ? 'text-warn' : 'text-ink-2'
                }`}
              >
                {countLabel(fallbacks)}
              </span>
            </span>
          </div>

          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[560px] border-collapse text-left text-[11px]">
              <thead>
                <tr className="border-b border-rule text-[10px] uppercase tracking-[0.1em] text-muted">
                  <th scope="col" className="py-1.5 pr-3 font-medium">
                    Stage
                  </th>
                  <th scope="col" className="py-1.5 pr-3 font-medium">
                    Route
                  </th>
                  <th scope="col" className="py-1.5 pr-3 font-medium">
                    Tokens
                  </th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-medium">
                    Cost
                  </th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-medium">
                    Latency
                  </th>
                  <th scope="col" className="py-1.5 text-right font-medium">
                    When
                  </th>
                </tr>
              </thead>
              <tbody>
                {calls.map((call, index) => {
                  const failed = String(call.status || '') !== 'ok';
                  return (
                    <tr
                      key={`${call.bifrost_request_id || 'call'}-${index}`}
                      className="border-b border-rule/60 last:border-0 align-baseline"
                    >
                      <td className="py-1.5 pr-3 text-ink-2">
                        {STAGE_LABEL[call.stage] || call.stage || '—'}
                        {Number(call.fallback_attempts) > 0 ? (
                          <span
                            className="ml-1.5 rounded-full border border-warn/40 bg-warn-soft px-1.5 py-0.5 text-[9.5px] text-warn"
                            title="Bifrost answered from a fallback provider"
                          >
                            fallback {countLabel(call.fallback_attempts)}
                          </span>
                        ) : null}
                        {failed ? (
                          <span
                            className="ml-1.5 rounded-full border border-danger/35 bg-danger-soft px-1.5 py-0.5 text-[9.5px] text-danger"
                            title={String(call.status || 'the call did not succeed')}
                          >
                            failed
                          </span>
                        ) : null}
                      </td>
                      <td
                        className="max-w-[220px] truncate py-1.5 pr-3 font-mono text-[10.5px] text-ink-2"
                        title={gatewayRoute(call)}
                      >
                        {gatewayRoute(call)}
                      </td>
                      <td className="py-1.5 pr-3 text-ink-2">
                        <Tokens call={call} />
                      </td>
                      <td className="py-1.5 pr-3 text-right font-mono tabular-nums text-ink-2">
                        {formatCost(call.cost_usd)}
                      </td>
                      <td className="py-1.5 pr-3 text-right font-mono tabular-nums text-ink-2">
                        {call.latency_ms == null ? '—' : `${countLabel(call.latency_ms)} ms`}
                      </td>
                      <td
                        className="py-1.5 text-right font-mono text-[10.5px] text-muted"
                        title={String(call.created_at || '')}
                      >
                        {call.created_at
                          ? `${shortDate(call.created_at)} ${timeOnly(call.created_at)}`
                          : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <Empty title="No model calls recorded">
          This run has not asked the gateway for anything yet. A run that settles
          claims without a model call is not a run this product can explain.
        </Empty>
      )}
    </section>
  );
}
