import { Link } from 'react-router-dom';
import { CopyButton } from '../ui.jsx';
import { num, when } from '../../lib/format.js';
import DeleteDatasetButton from './DeleteDatasetButton.jsx';

/**
 * What the request asked about in time, in one short phrase.
 *
 * `unknown`/`none` render nothing: a dataset collected from a prompt with no
 * time constraint should not claim one. A window is shown as its real bounds
 * rather than as "last 6 months", because the phrase that produced it may be
 * read months after the run.
 */
function temporalLabel(temporal) {
  const t = temporal || {};
  if (t.kind === 'current') {
    return { short: `as of ${t.as_of || 'today'}`, full: `as of ${t.as_of || 'today'}` };
  }
  if (t.kind === 'point' && t.as_of) {
    return { short: `as of ${t.as_of}`, full: `as of ${t.as_of}` };
  }
  if (t.kind === 'window' && t.start && t.end) {
    return {
      short: `${t.start} → ${t.end}`,
      full: `the window ${t.start} to ${t.end}`,
    };
  }
  return null;
}

/**
 * Dataset identity — one block, then the measurements.
 *
 * The old header stacked a label, a title, an id, a creation time, a run link,
 * four stat cards and an evidence bar before the reader reached a single record.
 * All of it was true and none of it was the point of opening a dataset.
 *
 * So the title leads, the counts sit on one line as a fact about the collection
 * rather than as four separate scores, and the run link — the thing a reader
 * actually follows when they want to know *how* this was gathered — is on that
 * same line instead of in a metadata row of its own.
 *
 * The `Raw` action is kept but demoted: it opens the untyped payload, which is
 * genuinely useful and genuinely not the first thing anyone wants.
 */

export default function DatasetHeader({ identity, model, temporal, onInspectRaw, onDeleted, className = '' }) {
  const { id, name, runId, createdAt, recordCount, fieldCount } = identity;
  const title = name || (id ? `Dataset ${String(id).slice(0, 8)}` : 'Dataset');
  const when_ = temporalLabel(temporal);
  const temp = (model?.counters?.temporal) || {};

  return (
    <header className={className}>
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <p className="eyebrow">Dataset</p>
          <h1 className="mt-1 h-display text-[30px] leading-[1.1] text-ink balance">{title}</h1>

          {/* One line of fact, not four cards. A record count and a field count
              are properties of the same collection, and setting them as
              separate 19px figures made a 120-record dataset look like a
              dashboard. */}
          <p className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 font-mono text-[11px] text-muted">
            <span className="tnum text-ink-2">{num(recordCount ?? 0)} records</span>
            <span aria-hidden="true">·</span>
            <span className="tnum text-ink-2">{num(fieldCount || 0)} fields</span>
            {model?.summary?.pages ? (
              <>
                <span aria-hidden="true">·</span>
                <span className="tnum text-ink-2">{num(model.summary.pages)} pages</span>
              </>
            ) : null}
            {runId ? (
              <>
                <span aria-hidden="true">·</span>
                <Link to={`/runs/${runId}`} className="link">
                  from run {String(runId).slice(0, 8)}
                </Link>
              </>
            ) : null}
            {createdAt ? (
              <>
                <span aria-hidden="true">·</span>
                <span>collected {when(createdAt)}</span>
              </>
            ) : null}
            {when_ ? (
              <>
                <span aria-hidden="true">·</span>
                {/*
                  The time the request asked about, not when we collected. A
                  reader must not mistake "as of 2024-09-29" for the fetch date,
                  which is why the label says "asked for".
                */}
                <span
                  className="text-info"
                  title={`The request asked for values ${when_.full}. Dates on individual cells are the pages' own publication dates.`}
                >
                  asked for {when_.short}
                </span>
              </>
            ) : null}
          </p>

          {/* A verified quote and a wrong date are both possible at once. The
              window is claimed on the line above, so the count that disagrees
              with it belongs here rather than buried in a per-cell tooltip. */}
          {temp.counted && temp.offWindow ? (
            <p className="mt-1 text-[10.5px] text-warn">
              {num(temp.offWindow)} dated value{temp.offWindow === 1 ? '' : 's'} fall
              outside the requested window
              {temp.before && temp.after
                ? ` (${num(temp.before)} before, ${num(temp.after)} after)`
                : temp.before
                ? ' (before it)'
                : ' (after it)'}
              . The quotes can still verify; the source date is what disagrees.
            </p>
          ) : null}

          {/* When the profile was read from a prefix rather than the whole
              dataset, anything drawn from it says so. A shape computed from
              the first 2,000 records of a 40,000-record dataset is a sample,
              and presenting it as the dataset is the difference between a
              summary and a lie. */}
          {model?.sampled && model.recordsRead ? (
            <p className="mt-1 text-[10.5px] text-muted">
              Shapes below are read from the first {num(model.recordsRead)} record
              {model.recordsRead === 1 ? '' : 's'}.
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {id ? <CopyButton value={id} label="Copy ID" /> : null}
          {onInspectRaw ? (
            <button type="button" onClick={onInspectRaw} className="btn-ghost btn-xs">
              Raw
            </button>
          ) : null}
          {id ? (
            <DeleteDatasetButton
              datasetId={id}
              recordCount={recordCount}
              onDeleted={onDeleted}
            />
          ) : null}
        </div>
      </div>
    </header>
  );
}