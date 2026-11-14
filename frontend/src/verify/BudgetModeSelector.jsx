/**
 * BudgetModeSelector — quota-aware research budget mode selector.
 *
 * Lets the operator choose how aggressively the pipeline spends free-tier
 * quota. The mode changes the research budget, not the truth standard:
 * Conserve mode must never lower the evidence threshold for `supported` or
 * `changed` — if it cannot afford sufficient research, it must abstain.
 *
 * Rendered as a compact ledger control rather than a card: the mode is one
 * line of context above the evidence, not a settings form in the middle of it.
 */
import { useState, useEffect } from 'react';
import { api } from '../lib/api';

const MODES = [
  { value: 'balanced', label: 'Balanced', desc: 'Default. Reuse cached evidence, basic search, targeted escalation.' },
  { value: 'conserve', label: 'Conserve', desc: 'Prefer cached evidence. One search per unresolved claim, limited extraction.' },
  { value: 'deep', label: 'Deep', desc: 'Additional targeted searches and independent corroboration. Respects hard quota ceilings.' },
  { value: 'offline', label: 'Offline', desc: 'No external API calls. Reuse stored sources and cached responses only.' },
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
    <div className="budget" role="group" aria-label="Research budget mode">
      <span className="budget-label">budget</span>
      {MODES.map((item) => {
        const selected = current === item.value;
        return (
          <button
            key={item.value}
            type="button"
            onClick={() => handleSelect(item.value)}
            disabled={saving}
            aria-pressed={selected}
            title={item.desc}
            className={`budget-btn ${selected ? 'is-on' : ''} ${
              item.value === 'offline' ? 'is-offline' : ''
            }`}
          >
            {item.label}
          </button>
        );
      })}
      {saving ? (
        <span className="budget-state" aria-live="polite">
          saving…
        </span>
      ) : null}
    </div>
  );
}
