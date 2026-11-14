/**
 * Shared pieces for the verification workspace.
 *
 * The status palette is the product's most important visual decision: the four
 * verdicts must be distinguishable at a glance and must not imply a fifth
 * ("partly true"). `changed` is deliberately not green — the claim was true and
 * is now stale, which is a different outcome from `supported`.
 */
import { useId, useState } from 'react';
import {
  RUN_STATE,
  STATUS,
  freshnessTone,
  statusMeta,
} from './model.js';

export function StatusPill({ status, size = 'md' }) {
  const meta = statusMeta(status);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-medium ${
        meta.cls
      } ${size === 'sm' ? 'px-2 py-0.5 text-[10.5px]' : 'px-2.5 py-1 text-[11.5px]'}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} aria-hidden="true" />
      {meta.label}
    </span>
  );
}

export function RunStatePill({ status }) {
  const live = status === 'running' || status === 'queued';
  const tone =
    status === 'completed'
      ? 'border-ok/35 bg-ok-soft text-ok'
      : status === 'partial'
        ? 'border-warn/40 bg-warn-soft text-warn'
        : status === 'failed'
          ? 'border-danger/35 bg-danger-soft text-danger'
          : 'border-rule-2 bg-paper-2 text-muted';
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] font-medium ${tone}`}
    >
      {live ? (
        <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
          <span className="absolute inline-flex h-full w-full animate-pulse-ring rounded-full bg-accent" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent" />
        </span>
      ) : (
        <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
      )}
      {RUN_STATE[status] || status}
    </span>
  );
}

export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint ? (
        <span className="mt-1 block text-[11px] leading-snug text-muted">{hint}</span>
      ) : null}
    </label>
  );
}

export function DateRow({ label, value, tone = '', title }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-rule/70 py-1.5 last:border-0">
      <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-muted">{label}</span>
      <span
        className={`text-right font-mono text-[11px] tabular-nums ${tone || 'text-ink-2'}`}
        title={title}
      >
        {value || '—'}
      </span>
    </div>
  );
}

export function SectionHeading({ children, aside, id }) {
  return (
    <div className="panel-head">
      <h2 id={id} className="panel-title">
        {children}
      </h2>
      {aside ? <div className="panel-aside">{aside}</div> : null}
    </div>
  );
}

export function Empty({ title, children }) {
  return (
    <div className="grid place-items-center px-6 py-12 text-center">
      <div className="max-w-sm">
        <p className="font-display text-[19px] text-ink">{title}</p>
        {children ? (
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted">{children}</p>
        ) : null}
      </div>
    </div>
  );
}

/** Accessible tab strip. No animation on purpose: a tab is a state change. */
export function Tabs({ tabs, value, onChange, label }) {
  const base = useId();
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 border-b border-rule">
      {tabs.map((tab) => {
        const active = tab.id === value;
        return (
          <button
            key={tab.id}
            id={`${base}-${tab.id}`}
            type="button"
            role="tab"
            aria-selected={active}
            aria-controls={`${base}-${tab.id}-panel`}
            onClick={() => onChange(tab.id)}
            className={`relative -mb-px border-b-2 px-3 py-2 text-[11.5px] font-medium transition-colors duration-150 ${
              active
                ? 'border-accent text-ink'
                : 'border-transparent text-muted hover:text-ink'
            }`}
          >
            {tab.label}
            {tab.count ? (
              <span className="ml-1.5 rounded-full bg-warm px-1.5 py-0.5 text-[10px] tabular-nums text-ink-2">
                {tab.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({ id, active, children }) {
  if (!active) return null;
  return (
    <div role="tabpanel" id={id} className="animate-fade-in">
      {children}
    </div>
  );
}

/** A copy-to-clipboard control that says what it did, then stops saying it. */
export function CopyButton({ text, label = 'Copy', className = 'btn-outline btn-xs' }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(String(text ?? ''));
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };
  return (
    <button type="button" className={className} onClick={copy}>
      {copied ? 'Copied' : label}
    </button>
  );
}

export function SkeletonBlock({ lines = 3 }) {
  return (
    <div className="space-y-2" role="status" aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }).map((_, index) => (
        <div key={index} className="skeleton h-3" style={{ width: `${100 - index * 12}%` }} />
      ))}
    </div>
  );
}

/**
 * One line of an infrastructure ledger: a mono label, the reading, a figure.
 *
 * Quota and gateway diagnostics are key/value lists, not card grids. A ruled
 * row keeps the label and its value on the same baseline, which is the only
 * reason a table of numbers is readable at 10px.
 */
export function LedgerRow({ label, meta, hint, children }) {
  return (
    <div className="infra-row">
      <span className="infra-label">{label}</span>
      <span className="min-w-0">
        {children}
        {hint ? <span className="infra-hint">{hint}</span> : null}
      </span>
      <span className="infra-meta">{meta}</span>
    </div>
  );
}

const FRESH_FILL = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  muted: 'bg-rule-2',
};

/**
 * The freshness score, as a bar and a number.
 *
 * `index === null` renders an empty track and an em dash, never `0`: a claim
 * that was never verified has no score to show.
 */
export function FreshnessMeter({ index, size = 'md' }) {
  const scored = index !== null && index !== undefined && Number.isFinite(Number(index));
  const value = scored ? Math.max(0, Math.min(100, Number(index))) : 0;
  const tone = freshnessTone(index);
  return (
    <span className={`fresh ${size === 'sm' ? 'is-sm' : ''}`}>
      <span className="fresh-bar" aria-hidden="true">
        <span className={`fresh-fill ${FRESH_FILL[tone]}`} style={{ width: `${value}%` }} />
      </span>
      <span className="fresh-value">{scored ? value : '—'}</span>
    </span>
  );
}

export const STATUS_ORDER = ['supported', 'changed', 'contradicted', 'unverifiable', 'pending'];

export const STATUS_COLOR = STATUS;
