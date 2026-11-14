/**
 * Illustrative content for the home page.
 *
 * Every case here is lifted from `eval/gold.jsonl`, so the claims, quotes,
 * dates and reason codes are the ones the engine is actually graded on. The run
 * ids, source domains and timestamps are the fixture's own `*.example` hosts —
 * this page is deliberately offline and never presents fixture data as a live
 * run. Anything that is a measurement is marked with where it came from.
 */

export const RULE_VERSION = 'tev-1.2.0';

/** The four outcomes. `changed` is amber, never green: the claim was true. */
export const VERDICTS = {
  supported: {
    label: 'Supported',
    tone: 'ok',
    reason: 'supported_by_source',
    line: 'A locatable passage states the same fact.',
  },
  changed: {
    label: 'Changed',
    tone: 'info',
    reason: 'value_changed_after_generation',
    line: 'A later, settled source states a replacement value.',
  },
  contradicted: {
    label: 'Contradicted',
    tone: 'danger',
    reason: 'contradicted_by_source',
    line: 'A locatable passage refutes the claim.',
  },
  unverifiable: {
    label: 'Unverifiable',
    tone: 'muted',
    reason: 'no_entailment',
    line: 'Nothing settled the question either way.',
  },
};

/**
 * The verdict deck. One case per outcome, each with the quote that decided it
 * and the two dates that had to be told apart to get there.
 */
export const CASES = [
  {
    id: 'supported',
    status: 'supported',
    reason: 'supported_by_source',
    prompt: 'How tall is the Eiffel Tower?',
    claim: 'The Eiffel Tower is 330 metres tall.',
    generatedAt: '2026-08-02T18:05:00Z',
    quote: 'The Eiffel Tower is 330 metres tall.',
    source: { url: 'facts.example/eiffel-tower', title: 'Eiffel Tower facts' },
    publishedDate: '2026-07-01',
    eventDate: null,
    retrievedAt: '2026-08-02T18:06:12Z',
    why: 'The quote is stored, its offsets resolve against that stored revision, and no later settled source replaces the value.',
    notThis: 'Not changed — a stable fact does not go stale because the page that states it is old.',
  },
  {
    id: 'changed',
    status: 'changed',
    reason: 'value_changed_after_generation',
    prompt: 'What does Acme Pro cost?',
    claim: 'Acme Pro costs $99 per month.',
    generatedAt: '2026-10-01T09:00:00Z',
    quote: 'Effective 2026-10-05, Acme Pro costs $129 per month.',
    source: { url: 'acme.example/pricing', title: 'Acme pricing' },
    publishedDate: '2026-10-06',
    eventDate: '2026-10-05',
    retrievedAt: '2026-10-06T11:20:04Z',
    replacement: '$129/month',
    highlight: '$129',
    proposal: 'contradicts',
    settlement: 'settled · replacement value dated 2026-10-05',
    why: 'The replacement is settled, its event date (2026-10-05) falls after the answer was generated (2026-10-01), and the quote resolves in the stored revision.',
    notThis: 'Not contradicted — there is a replacement value with a date, not a bare refutation.',
  },
  {
    id: 'contradicted',
    status: 'contradicted',
    reason: 'contradicted_by_source',
    prompt: 'What does Acme Pro cost?',
    claim: 'Acme Pro costs $99 per month.',
    generatedAt: '2026-10-01T09:00:00Z',
    quote: 'Acme Pro costs $129 per month as of 2026-09-15.',
    source: { url: 'acme.example/status', title: 'Acme pricing status' },
    publishedDate: '2026-09-20',
    eventDate: '2026-09-15',
    retrievedAt: '2026-09-20T08:41:55Z',
    replacement: '$129/month',
    highlight: '$129',
    proposal: 'contradicts',
    settlement: 'settled · refutation predates generation',
    why: 'The refutation is settled, but its event date (2026-09-15) predates the answer. The answer was already wrong when it was written, so it is not a change.',
    notThis: 'Not changed — a change requires the replacement to happen after generation.',
  },
  {
    id: 'unverifiable',
    status: 'unverifiable',
    reason: 'no_entailment',
    prompt: 'What does Acme Pro cost?',
    claim: 'Acme Pro costs $99 per month.',
    generatedAt: '2026-10-01T09:00:00Z',
    quote: 'Acme Pro is a subscription plan for small teams.',
    source: { url: 'acme.example/product', title: 'Acme Pro product page' },
    publishedDate: '2026-09-10',
    eventDate: null,
    retrievedAt: '2026-09-10T14:02:31Z',
    highlight: null,
    proposal: 'mentions_only',
    settlement: 'not settled · quote does not entail the value',
    why: 'The page names the subject and never states the value. A locatable mention that does not entail the claim establishes nothing.',
    notThis: 'Not contradicted — nothing found is an abstention, not a refutation.',
  },
];

/**
 * The ledger. A reader scanning this should be able to see that the engine
 * distinguishes cases that look identical in a chat transcript.
 */
export const LEDGER = [
  {
    claim: 'Acme Pro costs $99 per month.',
    status: 'changed',
    reason: 'value_changed_after_generation',
    event: '2026-10-05',
    generated: '2026-10-01',
    quote: 'Effective 2026-10-05, Acme Pro costs $129 per month.',
    source: 'acme.example/pricing',
  },
  {
    claim: 'Acme Pro costs $99 per month.',
    status: 'supported',
    reason: 'supported_by_source',
    event: '2026-08-01',
    generated: '2026-10-01',
    quote: 'Acme Pro costs $79 per month as of 2026-08-01.',
    source: 'roundup.example/acme',
  },
  {
    claim: 'Acme Pro costs $99 per month.',
    status: 'contradicted',
    reason: 'contradicted_by_source',
    event: '2026-09-15',
    generated: '2026-10-01',
    quote: 'Acme Pro costs $129 per month as of 2026-09-15.',
    source: 'acme.example/status',
  },
  {
    claim: 'Acme Pro costs $99 per month.',
    status: 'unverifiable',
    reason: 'no_source',
    event: null,
    generated: '2026-10-01',
    quote: 'No stored revision was retrieved for this claim.',
    source: 'search · 0 usable results',
  },
  {
    claim: 'The Eiffel Tower is 330 metres tall.',
    status: 'supported',
    reason: 'supported_by_source',
    event: null,
    generated: '2026-08-02',
    quote: 'The Eiffel Tower is 330 metres tall.',
    source: 'facts.example/eiffel-tower',
  },
  {
    claim: 'Acme Pro includes audit logs.',
    status: 'unverifiable',
    reason: 'conflicting_evidence',
    event: null,
    generated: '2026-10-01',
    quote: 'Two first-party pages disagree at the same effective date; neither is preferred.',
    source: 'acme.example/pricing · acme.example/status',
  },
  {
    claim: 'Acme Pro has been discontinued.',
    status: 'contradicted',
    reason: 'contradicted_by_source',
    event: '2026-10-06',
    generated: '2026-10-01',
    quote: 'Acme Pro remains available to new customers as of 2026-10-06.',
    source: 'acme.example/status',
  },
  {
    claim: 'Acme Pro costs €89 per month.',
    status: 'supported',
    reason: 'supported_by_source',
    event: null,
    generated: '2026-10-01',
    quote: 'Acme Pro costs €89 per month on the EU price list.',
    source: 'acme.example/eu-pricing',
  },
];

export const TIMESTAMPS = [
  {
    key: 'generated',
    label: 'Answer generated',
    field: 'answers.generated_at',
    line: 'When the model produced the text. The clock every change is measured against.',
    sources: 'user-supplied',
  },
  {
    key: 'published',
    label: 'Source published',
    field: 'source_revisions.published_date',
    line: 'When the page said it was published. Often years after the fact it describes.',
    sources: 'page metadata',
  },
  {
    key: 'event',
    label: 'Fact changed',
    field: 'evidence_spans.event_date',
    line: 'When the underlying fact actually changed. Unknown is `missing` — never inferred.',
    sources: 'quote text',
  },
  {
    key: 'retrieved',
    label: 'We fetched it',
    field: 'source_revisions.retrieved_at',
    line: 'When this revision entered the store. Says nothing about when the fact was true.',
    sources: 'run clock',
  },
];

/**
 * Two runs that look identical if you only read publication dates.
 * Publication is after generation in both. Only one is a change.
 */
export const TIMELINE_SCENARIOS = [
  {
    id: 'change',
    label: 'A price that moved',
    prompt: 'What does Acme Pro cost?',
    generated: '2026-10-01',
    published: '2026-10-06',
    event: '2026-10-05',
    retrieved: '2026-10-06',
    status: 'changed',
    reason: 'value_changed_after_generation',
    reading: 'Event 2026-10-05 is later than generation 2026-10-01, and a settled source states the replacement.',
  },
  {
    id: 'restatement',
    label: 'A newer page, an older fact',
    prompt: 'What does Acme Pro cost?',
    generated: '2026-10-01',
    published: '2026-10-09',
    event: '2026-08-01',
    retrieved: '2026-10-09',
    status: 'supported',
    reason: 'supported_by_source',
    reading: 'The page is new; the fact it states is older than the answer. Publication date is not evidence of a change.',
  },
];

export const STATS = [
  {
    value: 60.4,
    suffix: '%',
    decimals: 1,
    label: 'independent assertion recall',
    detail: '32 of 53 material in-scope assertions, annotated without the scanner’s output.',
    source: 'eval/independent · offline',
  },
  {
    value: 88.9,
    suffix: '%',
    decimals: 1,
    label: 'scanner precision',
    detail: '32 of 36 extracted candidates matched an annotated value exactly.',
    source: 'eval/independent · offline',
  },
  {
    value: 4,
    suffix: '',
    decimals: 0,
    label: 'verdicts, no fifth',
    detail: 'No “partly true”. A failed support is not a contradiction, and abstention is a real outcome.',
    source: 'app/verify/decision.py',
  },
  {
    value: 0,
    suffix: '',
    decimals: 0,
    label: 'verdicts written by a model',
    detail: 'The model proposes a relationship; stored rows and code assign the status.',
    source: 'app/verify/settlement.py',
  },
];

/** The vocabulary band under the hero. Decorative, and labelled as such. */
export const TICKER = [
  'value_changed_after_generation',
  'supported_by_source',
  'publication ≠ event',
  'contradicted_by_source',
  'no_source',
  'event_date · missing',
  'no_entailment',
  'settled · locator resolved',
  'temporal_ambiguity',
  'stored revision, not a snippet',
  'conflicting_evidence',
  'generation_time_unknown',
];

export const STEPS = [
  {
    n: '01',
    title: 'Two inputs, nothing else',
    body: 'The answer as it was written, and the moment it was generated. No schema, no labels, no prompt engineering.',
  },
  {
    n: '02',
    title: 'Claims, not paragraphs',
    body: 'The answer is segmented into atomic claims with a subject, a predicate and a checkable value — so one wrong price does not condemn a correct sentence.',
  },
  {
    n: '03',
    title: 'Discovery, then the store',
    body: 'Tavily returns candidates. Each page is fetched and written as an append-only source revision before a single quote is read out of it.',
  },
  {
    n: '04',
    title: 'The model proposes a relationship',
    body: 'supports, contradicts, mentions only, or silent — with a quote and the offsets it came from. Never a status.',
  },
  {
    n: '05',
    title: 'Code settles the cell',
    body: 'A verdict needs a support proposal and a locator that resolves against the exact stored text. Then, and only then, does the status exist.',
  },
];

export const CAPABILITIES = [
  ['Atomic claims', 'One verdict per checkable assertion, not per answer.'],
  ['Stored quotes', 'Every citation points at a revision row, never at a snippet.'],
  ['Event dates', 'Separate from publication and retrieval, and never back-filled.'],
  ['Reason codes', 'A machine-readable why, not a paragraph of prose.'],
  ['Durable event log', 'Search, storage and model failures are reported, not swallowed.'],
  ['Replayable runs', 'Cached model and research calls make a demo survive a bad network.'],
];
