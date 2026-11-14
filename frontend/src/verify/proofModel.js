/**
 * The proof graph as data, not as SVG coordinates.
 *
 * The previous implementation placed nodes at `40 + (i % 3) * 150` inside a
 * fixed 520×260 viewBox. At four evidence rows the last row was clipped, at two
 * the graph was mostly empty, and in the 390px inspector column it always
 * overflowed sideways. Layout that has to be recomputed per node count is the
 * part that was wrong, so the decision of *what the graph is* lives here and the
 * component only draws it.
 *
 * Plain `.js` on purpose: `node` imports it directly in the test suite.
 */

const RELATIONSHIP_COPY = {
  supports: 'supports',
  contradicts: 'contradicts',
  mentions_only: 'mentions only',
  silent: 'silent',
};

export function relationshipLabel(relationship) {
  const key = String(relationship || '').toLowerCase();
  if (RELATIONSHIP_COPY[key]) return RELATIONSHIP_COPY[key];
  return key ? key.replaceAll('_', ' ') : 'no proposal';
}

/**
 * Tone follows what the settlement gate accepted, not what the model proposed.
 *
 * A rejected `supports` is still an unaccepted edge: colouring it green would
 * draw a claim→evidence line the decision table never agreed to.
 */
export function relationshipTone(node) {
  if (!node?.accepted) return 'muted';
  if (node.relationship === 'supports') return 'ok';
  if (node.relationship === 'contradicts') return 'danger';
  if (node.relationship === 'mentions_only') return 'info';
  return 'muted';
}

function isoDay(value) {
  const text = String(value || '').slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : '';
}

/**
 * Nodes for one claim: one branch per stored evidence row.
 *
 * Dated rows are ordered by event date, oldest first, because the graph argues
 * that a fact changed over time. Rows with no event date cannot be placed on
 * that line, so they go last and say so — they are not silently dropped, and
 * they are not positioned as if the publication date were the event date.
 */
export function buildProofNodes(claim, evidence = []) {
  const rows = Array.isArray(evidence) ? evidence : [];
  const nodes = rows.map((item, index) => {
    const settledRel = typeof item?.relationship_settled === 'string'
      ? item.relationship_settled
      : '';
    const relationship = String(settledRel || item?.relationship_proposed || 'silent')
      .trim()
      .toLowerCase();
    const accepted = item?.settlement_status === 'accepted';
    const eventDate = isoDay(item?.event_date);
    const node = {
      id: item?.id || `ev-${index}`,
      index,
      quote: String(item?.quote_text || '').trim(),
      relationship,
      relationshipLabel: relationshipLabel(relationship),
      accepted,
      tone: 'muted',
      eventDate,
      eventMissing: !eventDate,
      eventStatus: String(item?.event_date_status || (eventDate ? 'explicit' : 'missing')),
      publishedDate: isoDay(item?.published_date),
      retrievedAt: String(item?.retrieved_at || ''),
      sourceUrl: String(item?.source_url || ''),
      sourceTitle: String(item?.source_title || ''),
      publisher: String(item?.publisher || ''),
      replacement: String(item?.candidate_value_raw || ''),
    };
    node.tone = relationshipTone(node);
    return node;
  });

  const dated = nodes
    .filter((node) => node.eventDate)
    .sort((left, right) => left.eventDate.localeCompare(right.eventDate));
  const undated = nodes.filter((node) => !node.eventDate);

  return {
    claimText: String(claim?.claim_text || ''),
    claimStatus: String(claim?.status || 'pending'),
    nodes: [...dated, ...undated],
    settled: nodes.filter((node) => node.accepted).length,
    dated: dated.length,
    undated: undated.length,
    contradicting: nodes.filter(
      (node) => node.accepted && node.relationship === 'contradicts',
    ).length,
  };
}

/** The one-line summary above the spine. Never invents a total it cannot count. */
export function proofSummary(model) {
  const parts = [];
  if (!model?.nodes?.length) return 'no evidence rows';
  parts.push(`${model.nodes.length} row${model.nodes.length === 1 ? '' : 's'}`);
  parts.push(`${model.settled} settled`);
  parts.push(
    model.undated
      ? `${model.dated} dated · ${model.undated} undated`
      : `${model.dated} dated`,
  );
  if (model.contradicting) parts.push(`${model.contradicting} contradicting`);
  return parts.join(' · ');
}
