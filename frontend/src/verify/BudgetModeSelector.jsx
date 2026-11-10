/**
 * BudgetModeSelector — quota-aware research budget mode selector.
 *
 * Lets the operator choose how aggressively the pipeline spends free-tier
 * quota. The mode changes the research budget, not the truth standard:
 * Conserve mode must never lower the evidence threshold for `supported` or
 * `changed` — if it cannot afford sufficient research, it must abstain.
 */
import { useState, useEffect } from 'react';
import { api } from '../lib/api';

const MODES = [
  { value: 'balanced', label: 'Balanced', tone: 'ok', desc: 'Default. Reuse cached evidence, basic search, targeted escalation.' },
  { value: 'conserve', label: 'Conserve', tone: 'warn', desc: 'Prefer cached evidence. One search per unresolved claim, limited extraction.' },
  { value: 'deep', label: 'Deep Research', tone: 'info', desc: 'Additional targeted searches and independent corroboration. Respects hard quota ceilings.' },
  { value: 'offline', label: 'Offline / Snapshot', tone: 'muted', desc: 'No external API calls. Reuse stored sources and cached responses only.' },
];

export default function BudgetModeSelector({ mode = 'balanced', onChange }) {
  const [current, setCurrent] = useState(mode);
  const [saving, setSaving] = useState(false);
  const [serverMode, setServerMode] = useState(mode);

  useEffect(() => {
    setCurrent(mode);
    setServerMode(mode);
  }, [mode]);

  const handleSelect = async (value) => {
    setCurrent(value);
    setSaving(true);
    try {
      const data = await api.governance.setBudgetMode({ mode: value });
      setServerMode(data.mode || value);
      onChange?.(data.mode || value);
    } catch (err) {
      // Revert on failure
      setCurrent(serverMode);
      console.error('Failed to set budget mode:', err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="surface p-4 space-y-3" aria-label="Research budget mode">
      <div className="flex items-center justify-between">
        <span className="label text-[11px] text-muted uppercase tracking-wider">Research budget mode</span>
        <span className={`font-mono text-[10px] ${current === 'offline' ? 'text-danger' : 'text-muted'}`}>
          {current}
        </span>
      </div>
      <div className="grid gap-1.5">
        {MODES.map((m) => {
          const selected = current === m.value;
          return (
            <button
              key={m.value}
              type="button"
              onClick={() => handleSelect(m.value)}
              disabled={saving}
              aria-pressed={selected}
              className={`flex items-center gap-2 rounded-md border px-3 py-2 text-[12px] transition-colors ${
                selected
                  ? 'border-accent bg-accent/10 text-accent'
                  : 'border-rule/30 bg-paper text-ink hover:border-accent/50'
              }`}
            >
              <div className="flex-1">
                <div className="flex items-center gap-1">
                  <span className={`font-medium ${selected ? 'text-ink' : 'text-ink-2'}`}>{m.label}</span>
                  {selected && (
                    <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
                  )}
                </div>
                <p className="text-[10px] text-ink-2">{m.desc}</p>
              </div>
              <span className={`text-[10px] font-mono ${selected ? 'text-accent' : 'text-muted'}`}>{m.value}</span>
            </button>
          );
        })}
      </div>
      {saving && (
        <div className="h-1 w-full bg-ok rounded-full animate-pulse" aria-hidden="true" />
      )}
    </div>
  );
}
