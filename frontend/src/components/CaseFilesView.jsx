import { useMemo, useState } from 'react';
import { api } from '../lib/api.js';
import useResource from '../hooks/useResource.js';
import { asField, canProve } from './evidence.jsx';
import { Empty, ErrorNote, Skeleton } from './ui.jsx';
import { num, truncate } from '../lib/format.js';

/**
 * The case file: a judgment read as a case, not as a row of cells.
 *
 * The ledger answers "what is on this record" for every record alike. A
 * judgment is not alike: its title and citation are a header, its parties and
 * case number are a caption, and its holding, order and reasoning are prose.
 * Reading that out of a twelve-column table loses the shape of the thing.
 *
 * So this view lists the records that carry a case file — the ones with a
 * `full_text` or `parties` cell — and shows the selected one as the document
 * it is. Every value here is still a stored cell with its own quote; the
 * "source" line under a field is the evidence, not decoration.
 *
 * A record with no case-file fields is not listed at all. An empty "no
 * parties" card for a statute would suggest the extraction failed, when the
 * statute simply is not a case.
 */

const ORDER = [
  ['parties', 'Parties'],
  ['case_number', 'Case number'],
  ['court', 'Court'],
  ['citation', 'Citation'],
  ['act', 'Act'],
  ['issues', 'Issues'],
  ['holding', 'Held'],
  ['disposal', 'Order'],
  ['cases_relied_on', 'Cases relied on'],
  ['acts_relied_on', 'Acts relied on'],
  ['appearances', 'Appearances'],
  ['procedural_history', 'How the case got here'],
];

function valueText(pf) {
  const v = pf?.value;
  if (v === null || v === undefined || v === '') return '';
  if (Array.isArray(v)) return v.join(' v. ');
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function Field({ name, label, cell }) {
  const pf = asField(cell);
  const text = valueText(pf);
  if (!text) return null;
  return (
    <div className="min-w-0">
      <dt className="eyebrow flex items-center gap-1.5">
        {label}
        {pf.status && pf.status !== 'verified' ? (
          <span className="normal-case tracking-normal text-warn">· {pf.status}</span>
        ) : null}
      </dt>
      <dd className="mt-0.5 whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-ink">
        {text}
      </dd>
      {pf.source?.quote ? (
        <dd className="mt-1 border-l-2 border-rule-2 pl-2 text-[11px] leading-snug text-muted">
          “{truncate(pf.source.quote, 220)}”
          {canProve(pf.source) ? (
            <span className="ml-1 text-ok">· re-locates at {pf.source.start}–{pf.source.end}</span>
          ) : null}
        </dd>
      ) : null}
    </div>
  );
}

function CaseFile({ record }) {
  const fields = record?.fields || {};
  const title = valueText(asField(fields.document_title)) || 'Untitled judgment';
  const citation = valueText(asField(fields.citation));
  const jurisdiction = valueText(asField(fields.jurisdiction));
  const tier = valueText(asField(fields.tier));
  const fullText = valueText(asField(fields.full_text));

  const shown = ORDER.filter(([name]) => fields[name]);

  return (
    <article className="space-y-5">
      <header className="border-b border-rule pb-3">
        <p className="eyebrow">
          Case file
          {tier ? <span> · {tier}</span> : null}
          {jurisdiction ? <span> · {jurisdiction}</span> : null}
        </p>
        <h2 className="mt-1 font-display text-[22px] leading-tight text-ink">{title}</h2>
        {citation ? <p className="mt-1 text-[12px] text-muted">{citation}</p> : null}
      </header>

      {shown.length ? (
        <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
          {shown.map(([name, label]) => (
            <Field key={name} name={name} label={label} cell={fields[name]} />
          ))}
        </dl>
      ) : (
        <p className="text-[12px] text-muted">
          This record has a body but none of the case-file fields could be read from it.
        </p>
      )}

      {fullText ? (
        <details className="surface p-3">
          <summary className="cursor-pointer text-[12px] font-medium text-ink">
            Full judgment text · {num(fullText.length)} characters
          </summary>
          <pre className="scroll-y mt-2 max-h-[60vh] overflow-x-auto whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-ink-2">
            {fullText}
          </pre>
        </details>
      ) : null}
    </article>
  );
}

export default function CaseFilesView({ datasetId }) {
  const [q, setQ] = useState('');
  const [fulltext, setFulltext] = useState(true);
  // A case file is a judgment: it cites its own body or names its parties.
  // The list is the dataset's records filtered here, not a server-side field
  // query, because "which records are cases" is a property of what was read
  // out of them rather than of a column the schema guarantees.
  const { data, status, error, refetch } = useResource(
    (o) => api.records(datasetId, { limit: 500, offset: 0, q: q.trim() || undefined, fulltext }, o),
    [datasetId, q, fulltext]
  );

  const cases = useMemo(
    () =>
      (data?.records || []).filter(
        (r) => r?.fields?.full_text || r?.fields?.parties
      ),
    [data]
  );
  const [pickedId, setPickedId] = useState(null);
  const picked = cases.find((r) => r.record_id === pickedId) || cases[0] || null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12px] text-muted">
          {num(cases.length)} case {cases.length === 1 ? 'file' : 'files'} in this dataset
        </p>
        <div className="flex items-center gap-1.5">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={fulltext ? 'Search inside the judgments…' : 'Search case fields…'}
            aria-label="Search case files"
            className="input-sm input w-56"
          />
          <label className="flex items-center gap-1.5 text-[11px] text-muted">
            <input
              type="checkbox"
              checked={fulltext}
              onChange={(e) => setFulltext(e.target.checked)}
              className="accent-accent"
            />
            body text
          </label>
          <button type="button" onClick={refetch} className="btn-outline btn-xs">
            Refresh
          </button>
        </div>
      </div>

      {error ? <ErrorNote error={error} onRetry={refetch} /> : null}

      <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
        <nav aria-label="Case files" className="surface max-h-[70vh] overflow-y-auto p-1">
          {status === 'loading' && !cases.length ? (
            <div className="space-y-2 p-2" aria-busy="true">
              {Array.from({ length: 6 }, (_, i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </div>
          ) : !cases.length ? (
            <p className="p-3 text-[12px] text-muted">No case files match this search.</p>
          ) : (
            <ul className="space-y-px">
              {cases.map((r) => {
                const t = valueText(asField(r.fields.document_title)) || 'Untitled';
                const on = r.record_id === picked?.record_id;
                return (
                  <li key={r.record_id}>
                    <button
                      type="button"
                      onClick={() => setPickedId(r.record_id)}
                      aria-current={on ? 'true' : undefined}
                      className={`focusable w-full rounded-xs px-2 py-1.5 text-left text-[12px] leading-snug transition-colors duration-150 ease-swift ${
                        on ? 'bg-warm/80 text-ink' : 'text-ink-2 hover:bg-warm/40'
                      }`}
                    >
                      {truncate(t, 70)}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </nav>

        <div className="min-w-0">
          {picked ? (
            <CaseFile record={picked} />
          ) : (
            <Empty
              title="No case file selected"
              hint="This dataset holds no record with a case-file shape."
            />
          )}
        </div>
      </div>
    </div>
  );
}
