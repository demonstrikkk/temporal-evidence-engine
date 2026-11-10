/**
 * ProofGraph — temporal proof graph visualization.
 *
 * Shows how a claim's conclusion evolved over time: original evidence,
 * replacement evidence, event dates, and settlement status. Users can inspect
 * how a conclusion changed rather than just see its current status.
 *
 * Renders as an SVG timeline: nodes are evidence items, edges are temporal
 * relationships (supports / contradicts / replacement).
 */
import { useMemo } from 'react';

export default function ProofGraph({ claim, evidence = [] }) {
  if (!claim || !evidence?.length) {
    return (
      <div className="surface p-4 text-[12px] text-muted">
        No proof graph available for this claim.
      </div>
    );
  }

  // Build nodes: claim + each accepted evidence item
  const nodes = useMemo(() => {
    const accepted = evidence.filter(
      (e) => e.settlement_status === 'accepted' || e.relationship_settled
    );
    const claimNode = {
      id: 'claim',
      label: claim.claim_text,
      type: 'claim',
      status: claim.status,
      x: 40,
      y: 60,
    };
    const evNodes = accepted.map((e, i) => ({
      id: `ev-${i}`,
      label: e.quote_text || '',
      source: e.source_title || e.source_url,
      type: 'evidence',
      settled: e.settlement_status === 'accepted',
      eventDate: e.event_date,
      x: 40 + (i % 3) * 150,
      y: 140 + Math.floor(i / 3) * 90,
    }));
    return [claimNode, ...evNodes];
  }, [claim, evidence]);

  // Build edges: claim -> each evidence (supports/contradicts)
  const edges = useMemo(() => {
    return nodes
      .filter((n) => n.type === 'evidence')
      .map((n) => ({
        from: 'claim',
        to: n.id,
        relationship: n.settled ? 'supports' : 'contradicts',
      }));
  }, [nodes]);

  const width = 520;
  const height = 260;

  return (
    <div className="surface p-4" aria-label="Temporal proof graph">
      <div className="flex items-center gap-2 mb-3">
        <span className="label text-[11px] text-muted uppercase tracking-wider">Temporal proof graph</span>
        <span
          className={`font-mono text-[10px] ${claim.status === 'changed' ? 'text-info' : claim.status === 'contradicted' ? 'text-danger' : 'text-ok'}`}
        >
          {claim.status}
        </span>
      </div>
      <div className="relative w-full h-[260px] overflow-auto">
        <svg
          width={width}
          height={height}
          className="rounded-md bg-paper/50 border border-rule/20"
          role="img"
          aria-label="Proof graph"
        >
          {/* Edges */}
          {edges.map((e) => (
            <line
              key={e.to}
              x1={nodes.find((n) => n.id === e.from)?.x || 40}
              y1={nodes.find((n) => n.id === e.from)?.y || 60}
              x2={nodes.find((n) => n.id === e.to)?.x || 40}
              y2={nodes.find((n) => n.id === e.to)?.y || 140}
              stroke={e.relationship === 'supports' ? '#0ea5e9' : '#ef4444'}
              strokeWidth="1.5"
              opacity={0.5}
              strokeDasharray="4 2"
            />
          ))}
          {/* Evidence nodes */}
          {nodes.map((n) => {
            const isClaim = n.type === 'claim';
            const settled = n.type === 'evidence' && n.settled;
            return (
              <g key={n.id} transform={`translate(${n.x}, ${n.y})`}>
                <rect
                  width={isClaim ? 120 : 110}
                  height={isClaim ? 52 : 72}
                  rx="6"
                  fill={isClaim ? '#f8fafc' : settled ? '#ecfeff' : '#fef2f2'}
                  stroke={isClaim ? '#475569' : settled ? '#0ea5e9' : '#ef4444'}
                  strokeWidth={isClaim ? 1.5 : 2}
                />
                <text
                  x="6"
                  y={isClaim ? 20 : 18}
                  fontSize="11"
                  fill="#475569"
                  fontWeight="600"
                  title={n.label}
                >
                  {isClaim ? 'Claim' : 'Evidence'}
                </text>
                <text
                  x="6"
                  y={isClaim ? 36 : 34}
                  fontSize="10"
                  fill="#64748b"
                  title={n.label || ''}
                >
                  {(n.label || '').slice(0, 40)}{(n.label || '').length > 40 ? '…' : ''}
                </text>
                {n.type === 'evidence' && (
                  <>
                    <text
                      x="6"
                      y={n.eventDate ? 50 : 48}
                      fontSize="9"
                      fill="#94a3b8"
                    >
                      {n.eventDate ? `Event: ${n.eventDate}` : 'No event date'}
                    </text>
                    <text
                      x="6"
                      y={n.eventDate ? 64 : 62}
                      fontSize="9"
                      fill={settled ? '#0ea5e9' : '#ef4444'}
                    >
                      {settled ? 'Settled: supports' : 'Not settled'}
                    </text>
                    <text
                      x="6"
                      y={n.eventDate ? 76 : 74}
                      fontSize="8"
                      fill="#94a3b8"
                      title={n.source || ''}
                    >
                      {(n.source || '').slice(0, 28)}
                    </text>
                  </>
                )}
              </g>
            );
          })}
          {/* Claim-to-evidence labels */}
          {edges.map((e) => {
            const from = nodes.find((n) => n.id === e.from);
            const to = nodes.find((n) => n.id === e.to);
            if (!from || !to) return null;
            return (
              <text
                key={e.to}
                x={(from.x + to.x) / 2}
                y={(from.y + to.y) / 2 - 4}
                fontSize="9"
                fill={e.relationship === 'supports' ? '#0ea5e9' : '#ef4444'}
                textAnchor="middle"
              >
                {e.relationship}
              </text>
            );
          })}
        </svg>
      </div>
      <p className="mt-2 text-[10px] text-muted">
        Nodes: claim and accepted evidence. Edges: settlement relationship.
        Green = supports, red = contradicts/not settled.
      </p>
    </div>
  );
}
