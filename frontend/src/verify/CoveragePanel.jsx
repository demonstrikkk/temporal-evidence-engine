/**
 * What the answer asserted, and what the run actually checked.
 *
 * The verdict register answers "was this claim true"; this panel answers the
 * different question "was this answer looked at". A run that checked two of
 * eleven assertions can be perfect on those two, so the gap has to be visible
 * without reading the event log.
 *
 * The summary arrives with the run; the span list is fetched once the run is
 * terminal because it is long and cannot change after the audit is recorded.
 */
import { useEffect, useMemo, useState } from 'react';
import { api } from '../lib/api';
import { SectionHeading, SkeletonBlock } from './ui.jsx';

const DISPOSITION_COPY = {
  checked: 'checked',
  subjective: 'opinion',
  prediction: 'prediction',
  not_checked: 'not checked',
};

function Ratio({ label, value, detail }) {
  const pct = Math.round((Number(value) || 0) * 1000) / 10;
  return (
    <div className="min-w-0">
      <p className="text-[10px] uppercase tracking-[0.14em] text-muted">{label}</p>
      <p className="mt-0.5 font-mono text-[19px] leading-none text-ink">{pct}%</p>
      <p className="mt-1 text-[10.5px] text-muted">{detail}</p>
    </div>
  );
}

export default function CoveragePanel({ runId, summary, answerText, live }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    if (live) {
      setDetail(null);
      return () => {
        alive = false;
      };
    }
    api.verify
      .coverage(runId)
      .then((data) => {
        if (!alive) return;
        setDetail(data);
        setError(null);
      })
      .catch((err) => {
        if (alive) setError(err);
      });
    return () => {
      alive = false;
    };
  }, [runId, live]);

  const audit = detail?.audit || summary;
  const uncovered = useMemo(
    () => (detail?.assertions || []).filter((row) => row.disposition === 'not_checked'),
    [detail],
  );

  if (!audit) {
    if (error) {
      return (
        <section className="surface p-4" aria-label="Assertion coverage">
          <SectionHeading>Coverage</SectionHeading>
          <p className="mt-2 text-[11.5px] text-muted">
            Coverage could not be loaded for this run.{' '}
            {error.message ? <span className="font-mono">{error.message}</span> : null}
          </p>
        </section>
      );
    }
    if (!live) return null;
    return (
      <section className="surface p-4" aria-label="Assertion coverage">
        <SectionHeading>Coverage</SectionHeading>
        <div className="mt-3">
          <SkeletonBlock lines={2} />
        </div>
      </section>
    );
  }

  const total = Number(audit.total_assertions) || 0;
  const checked = Number(audit.checked) || 0;
  const dispositions = audit.dispositions || {};
  const repairCalls = Number(audit.repair_calls) || 0;
  const repaired = Number(audit.repaired_claims) || 0;

  return (
    <section className="surface p-4" aria-label="Assertion coverage">
      <SectionHeading
        aside={
          total
            ? `${checked} of ${total} assertions carried by a claim`
            : 'No checkable values were found in the answer'
        }
      >
        Coverage
      </SectionHeading>

      <div className="mt-3 grid grid-cols-3 gap-4 border-y border-rule py-3">
        <Ratio
          label="Coverage"
          value={audit.coverage}
          detail={`${checked}/${total} assertions`}
        />
        <Ratio
          label="Material"
          value={audit.material_coverage}
          detail="opinions and predictions excluded"
        />
        <Ratio
          label="Uncovered"
          value={total ? (Number(dispositions.not_checked) || 0) / total : 0}
          detail={`${Number(dispositions.not_checked) || 0} not checked`}
        />
      </div>

      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {Object.entries(DISPOSITION_COPY).map(([key, label]) => {
          const count = Number(dispositions[key]) || 0;
          if (!count) return null;
          return (
            <span
              key={key}
              className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10.5px] ${
                key === 'checked'
                  ? 'border-rule bg-paper-2 text-ink-2'
                  : key === 'not_checked'
                    ? 'border-warn/40 bg-warn-soft text-warn'
                    : 'border-rule bg-paper text-muted'
              }`}
            >
              {label} {count}
            </span>
          );
        })}
        {repairCalls ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-rule bg-paper px-2 py-0.5 font-mono text-[10.5px] text-muted">
            repair {repairCalls} call{repairCalls === 1 ? '' : 's'} · {repaired} recovered
          </span>
        ) : null}
      </div>

      {uncovered.length ? (
        <details className="mt-3">
          <summary className="cursor-pointer text-[11.5px] text-ink-2">
            {uncovered.length} assertion{uncovered.length === 1 ? '' : 's'} no claim
            covered
          </summary>
          <ul className="mt-2 space-y-1.5">
            {uncovered.slice(0, 12).map((row, index) => {
              const sentence = String(answerText || '')
                .slice(Number(row.sentence_start) || 0, Number(row.sentence_end) || 0)
                .trim();
              return (
                <li key={`${row.start_char}-${index}`} className="text-[11.5px] text-muted">
                  <span className="font-mono text-warn">{row.text}</span>
                  {sentence ? <span> — “{sentence.slice(0, 160)}”</span> : null}
                </li>
              );
            })}
            {uncovered.length > 12 ? (
              <li className="text-[11px] text-muted">
                …and {uncovered.length - 12} more
              </li>
            ) : null}
          </ul>
        </details>
      ) : null}

      <p className="mt-3 text-[10.5px] leading-relaxed text-muted">
        A coverage disposition describes the extraction, not the fact. “Not checked”
        is a gap in the run, never a verdict.
      </p>
    </section>
  );
}
