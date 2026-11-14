/**
 * The playground's decisions, as data.
 *
 * Three of them are the kind that fail quietly and would be noticed only by
 * someone reading a receipt closely:
 *
 * - "no tool calls" and "tools were never offered" are different sentences, and
 *   one of them means the answer cannot have been researched;
 * - a key is shown masked, never echoed whole, because this panel sits on a
 *   projector during a demo;
 * - a scan's generation time is the model's own answer time, and if that is
 *   missing the scan must say "unknown" rather than defaulting to now — a
 *   defaulted timestamp would make every claim scannable for staleness when
 *   only some of them are.
 *
 * Plain `.js` on purpose: `node` imports it directly in the test suite.
 */

/** `sk-live-…9f2c`, or an honest "not set". */
export function maskKey(value) {
  const key = String(value || '').trim();
  if (!key) return 'not set';
  if (key.length <= 8) return `${key.slice(0, 2)}…`;
  return `${key.slice(0, 6)}…${key.slice(-4)}`;
}

/** The host and path, without the scheme, for a one-line label. */
export function endpointLabel(endpoint) {
  return String(endpoint || '')
    .replace(/^https?:\/\//, '')
    .replace(/\/$/, '') || 'no endpoint';
}

/** Tokens as a sentence, or "not reported" when the endpoint said nothing. */
export function usageLine(usage) {
  const input = Number(usage?.input_tokens) || 0;
  const output = Number(usage?.output_tokens) || 0;
  if (!input && !output) return 'usage not reported';
  return `${input} in · ${output} out`;
}

/**
 * What a gateway call cost, or a plain admission that it did not say.
 *
 * A per-call price is small enough that two decimal places would render every
 * cheap call as `$0.00`, which reads as free. Six is enough to be true.
 */
export function costLine(cost) {
  if (cost === null || cost === undefined || cost === '') return 'not reported';
  const value = Number(cost);
  if (!Number.isFinite(value)) return 'not reported';
  if (value === 0) return '$0.00';
  if (value < 0.01) return `$${value.toFixed(6)}`;
  return `$${value.toFixed(4)}`;
}

/**
 * Which model answered, and where the call actually went.
 *
 * A gateway receipt carries the model that *served* the call, which is not
 * always the one that was asked for — routing may fall back. Repeating the
 * request here would hide exactly that, so the resolved name wins.
 */
export function resultRoute(result) {
  const gateway = result?.source === 'gateway';
  const model = (gateway ? result?.resolved_model || result?.model : result?.model) || '';
  if (gateway) {
    const provider = String(result?.provider || '');
    return {
      gateway: true,
      model,
      where: provider ? `${provider} · via the gateway` : 'via the gateway',
    };
  }
  return { gateway: false, model, where: endpointLabel(result?.endpoint) };
}

/**
 * One tool call's verdict.
 *
 * `ok` is the receipt's own flag, not a guess from `results`. A search that
 * returned zero rows succeeded and found nothing; a search that timed out
 * failed. Collapsing those two is how a receipt starts lying.
 */
export function toolOutcome(receipt) {
  if (receipt?.ok) {
    const results = Number(receipt.results) || 0;
    return {
      tone: results ? 'ok' : 'warn',
      label: results ? `${results} result${results === 1 ? '' : 's'}` : 'no results',
    };
  }
  return { tone: 'danger', label: receipt?.error || 'failed' };
}

/** The query a tool call ran, whatever shape the arguments arrived in. */
export function toolQuery(receipt) {
  const args = receipt?.arguments;
  if (args && typeof args === 'object') {
    return String(args.query || args.url || args.targets || '');
  }
  if (typeof args === 'string') return args.slice(0, 120);
  return '';
}

/**
 * What the tool loop did, in one line.
 *
 * The distinction that matters: `tools_enabled === false` means the answer was
 * written with no research at all, which is exactly the situation this product
 * exists to flag. Printing "0 tool calls" there would read as "it researched
 * and found nothing".
 */
export function toolSummary(result) {
  if (!result) return '';
  if (!result.tools_enabled) {
    return result.tools_available === false
      ? 'no tools: this deployment has no search key'
      : 'no tools: the toggle was off, so this answer used no live sources';
  }
  const calls = Array.isArray(result.tool_calls) ? result.tool_calls : [];
  if (!calls.length) return 'tools were offered and the model asked for none';
  const ok = calls.filter((call) => call?.ok).length;
  const failed = calls.length - ok;
  const pages = calls.reduce((total, call) => total + (Number(call?.results) || 0), 0);
  const parts = [`${ok} search${ok === 1 ? '' : 'es'}`, `${pages} result${pages === 1 ? '' : 's'} seen`];
  if (failed) parts.push(`${failed} failed`);
  return parts.join(' · ');
}

/**
 * The body `POST /api/verify/runs` expects.
 *
 * `generated_at_source` is `unknown` when the receipt carries no timestamp, and
 * the answer text is trimmed but never rewritten: the scanner must see the
 * model's words, not a cleaning of them.
 *
 * The model is the one that answered, not the one requested. The gateway
 * reports both, and only the answerer is a fact about the prose — a fallback
 * means the requested id would name the wrong model for these claims. A
 * bring-your-own endpoint reports no resolution, so the typed id is the only
 * thing that can be recorded; an empty value is honest when neither is known.
 */
export function scanPayload(result, question) {
  const generatedAt = String(result?.generated_at || '').trim();
  const answerer = String(result?.resolved_model || result?.model || '').trim();
  return {
    answer: String(result?.answer || '').trim(),
    prompt: String(question || '').trim(),
    generated_at: generatedAt || null,
    generated_at_source: generatedAt ? 'user_supplied' : 'unknown',
    skip_non_factual: true,
    model: answerer,
    provider: String(result?.provider || '').trim(),
  };
}

/** Whether a result can be scanned at all, and why not when it cannot. */
export function scanReadiness(result) {
  if (!result) return { ready: false, reason: 'Ask the model something first.' };
  if (!String(result.answer || '').trim()) {
    return { ready: false, reason: 'The model returned no text, so there is nothing to scan.' };
  }
  return { ready: true, reason: '' };
}

/**
 * The host of an endpoint, for naming the thing that refused a key.
 *
 * A base URL is typed by hand, so it is not always parseable. Falling back to
 * the text between the scheme and the first slash means the error still names
 * something real instead of "undefined".
 */
export function endpointHost(endpoint) {
  const value = String(endpoint || '').trim();
  if (!value) return '';
  try {
    return new URL(value).host;
  } catch {
    return value.replace(/^[a-z]+:\/\//i, '').split('/')[0];
  }
}

/**
 * Key prefixes that are distinctive enough to name the provider.
 *
 * Deliberately short. Mistral, DeepSeek and OpenAI all issue `sk-`-shaped keys
 * with no reliable separator, and guessing from an opaque string is how a UI
 * sends someone to the wrong console. Only a prefix with a real provider
 * signature belongs here.
 */
const KEY_FAMILIES = [
  { id: 'anthropic', label: 'Anthropic', test: /^sk-ant-/ },
  { id: 'openrouter', label: 'OpenRouter', test: /^sk-or-v1-/ },
  { id: 'groq', label: 'Groq', test: /^gsk_/ },
  { id: 'gemini', label: 'Google AI Studio', test: /^AIza[0-9A-Za-z_-]{10,}/ },
  { id: 'together', label: 'Together', test: /^tgp_v1_/ },
  { id: 'tavily', label: 'Tavily', test: /^tvly-/ },
];

/** `{ id, label }` for a recognisable key, else null. Never a guess. */
export function keyFamily(key) {
  const value = String(key || '').trim();
  if (!value) return null;
  for (const family of KEY_FAMILIES) {
    if (family.test.test(value)) return { id: family.id, label: family.label };
  }
  return null;
}

/**
 * Which preset a key appears to belong to, and whether that is where the page
 * is already pointed.
 *
 * `preset === null` with a known family means the key is for something this
 * client cannot call — an Anthropic key, or a Tavily search key pasted into the
 * wrong field. Saying so is more useful than "401".
 */
export function keySuggestion(key, presets, baseUrl) {
  const family = keyFamily(key);
  if (!family) return null;
  const target = (presets || []).find((item) => item.id === family.id) || null;
  const here = endpointHost(baseUrl).toLowerCase();
  const there = target ? endpointHost(target.base_url).toLowerCase() : '';
  return {
    family,
    preset: target,
    matchesEndpoint: Boolean(there) && here === there,
  };
}

/**
 * A provider's refusal without the internal error code in front of it.
 *
 * `E_PROVIDER_FATAL: The endpoint refused that API key` is two audiences in one
 * sentence. The page's reader needs the second half; the code belongs in a log.
 */
export function errorMessage(error) {
  const raw = error?.payload?.error?.message || error?.message || '';
  return String(raw).replace(/^E_[A-Z_]+:\s*/, '');
}

/** The machine-readable reason a provider failed, if the backend sent one. */
export function errorReason(error) {
  return String(error?.payload?.error?.details?.reason || '');
}
