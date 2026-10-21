import useResource from '../hooks/useResource.js';
import { api } from '../lib/api.js';
import { num } from '../lib/format.js';
import { Empty, ErrorNote, Loading, Pill } from './ui.jsx';

export default function BucketsView({ datasetId }) {
  const { data, status, error, refetch } = useResource((o) => api.buckets(datasetId, o), [datasetId]);
  if (status === 'loading' && !data) return <Loading rows={4} label="Reading buckets" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  if (!data) return <Empty title="No bucket data" hint="Buckets appear once a dataset has rules." />;

  const buckets = data.buckets || [];
  const unassigned = data.unassigned_record_ids || [];

  return (
    <div className="space-y-4">
      <p className="text-[12.5px] text-muted">
        Derived views over stored cells. Nothing here is written to a record.
      </p>
      {data.reason ? <p className="text-[12.5px] text-muted">{data.reason}</p> : null}
      <div className="grid gap-3 sm:grid-cols-2">
        {buckets.map((bucket) => (
          <section key={bucket.name} className="rounded-sm border border-rule p-3">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-[13px] font-medium text-ink">{bucket.label || bucket.name}</h3>
              <Pill tone={bucket.fallback ? 'muted' : 'info'}>{num(bucket.count)}</Pill>
            </div>
            <p className="mt-1 text-[11px] text-muted">
              {bucket.field}: {Object.keys(bucket.values || {}).join(', ') || 'all remaining records'}
            </p>
            <p className="mt-2 font-mono text-[10px] text-rule-2">
              {bucket.record_ids?.length ? `${bucket.record_ids.length} records` : 'No records'}
            </p>
          </section>
        ))}
      </div>
      {unassigned.length ? (
        <p className="text-[12px] text-muted">
          {num(unassigned.length)} record{unassigned.length === 1 ? '' : 's'} did not match any bucket.
        </p>
      ) : null}
    </div>
  );
}
