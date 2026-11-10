"""Pydantic domain schemas — mirror contracts/*.schema.json. Single source for API+DB."""
import re
from typing import Any, Literal
from pydantic import BaseModel, Field

FieldType = Literal["string","number","boolean","date","array","url"]
#: `judgment_unavailable` is deliberately distinct from `verified`. It means the
#: quote was found in the page but no judge was reachable to rule on it, which
#: used to be reported as `verified` and inflated every verification summary.
#: `rate_limited` is distinct again: the judge was there and said "wait". That
#: is an operational condition worth retrying, not an absence of evidence.
Verification = Literal["verified", "unverified", "conflicting",
                       "judgment_unavailable", "rate_limited"]


def is_identity_field(name: str) -> bool:
    """Is this field the *subject* of a record, rather than a property of one?

    The distinction decides whether a page-level value may fill it. A listing page
    declares exactly one `Organization` — the site publishing it — and a
    JSON-LD reader in document order reaches it first. So `company_name` on a
    TechCrunch listing page returned "Latest News | TechCrunch", stamped
    `verified` because a structured route is verified by construction.

    That is the worst possible failure: a confident, proven, wrong value. A
    property like `industry` or `founded` describes whatever entity the page is
    about, so page-level data is legitimate for those. An identity field is
    different — it must come from the record, not from the page furniture.
    """
    # `companyName` and `Company Name` reach this as readily as `company_name`,
    # so camelCase is split before the lookup. Plan fields are snake_case today,
    # but a JSON-LD property or a user-authored schema is not bound by that, and
    # a silent miss here means page furniture gets stored as a record again.
    raw = str(name or "").strip()
    raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw)
    n = raw.lower().replace("-", "_").replace(" ", "_")
    n = re.sub(r"_+", "_", n).strip("_")
    return n in _IDENTITY_FIELDS or n.endswith(("_name", "_names", "_title"))


#: Fields that name the subject of a record.
_IDENTITY_FIELDS = frozenset({
    "company", "companies", "company_name", "organisation", "organization",
    "organisation_name", "organization_name", "startup", "startup_name",
    "business", "business_name", "founder", "founders", "founder_name",
    "founder_names", "person", "person_name", "people", "author", "authors",
    "author_name", "author_names", "employer", "employer_name", "name",
    "names", "product", "product_name", "vendor", "supplier", "customer",
    "client", "publisher", "owner", "operator", "agency", "investor",
    "employee", "candidate", "title",
})


class FieldSpec(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_]+$")
    type: FieldType
    description: str = ""
    required: bool = False


class TemporalSpec(BaseModel):
    """The time a request asks about, parsed from the prompt.

    This is a *request* about time, not a claim about any one cell. It has to be
    declared on the plan because pydantic drops keys it does not know: a spec
    stamped on the dict but absent from this model would vanish on the next
    `WorkflowPlan(**plan).model_dump()` round trip, and the run would lose the
    window it was asked to answer for.

    `kind` is deliberately small:

    * `none`    the prompt named no time constraint;
    * `current` "as of today", "current", "latest" — a point, but one whose
                date is the run date rather than a date the user typed;
    * `point`   "as of 2023-09-29" — a single date;
    * `window`  "last 6 months", "between 2020 and 2023".

    A bare year in a prompt ("companies founded in 2020") is *not* parsed as a
    time constraint. It is a field filter, and treating it as an as-of date
    would silently discard every source outside that year. Only phrases that
    unambiguously describe the requested state of the world are read.
    """
    kind: Literal["none", "current", "point", "window"] = "none"
    #: ISO date the request is anchored to, for `point` and `current`.
    as_of: str = ""
    #: Inclusive ISO window bounds, for `window`.
    start: str = ""
    end: str = ""
    #: The prompt text that produced this, kept so a run can say why it has a
    #: window rather than making a reader reverse-engineer the dates.
    phrase: str = ""
    source: str = ""


class WorkflowPlan(BaseModel):
    goal: str
    entity: str
    requested_count: int = 15
    max_results: int = 15
    fields: list[FieldSpec]
    search_queries: list[str] = Field(min_length=1, max_length=5)
    seed_domains: list[str] = []
    seed_urls: list[str] = []
    source_types: list[str] = []
    traversal: dict = {"max_pages_per_domain": 10}
    validation_rules: list[str] = []
    dedupe_keys: list[str] = []
    allowed_sources: list[str] = []
    max_pages: int = 12
    #: The time the request asks about. Declared here for the same reason as the
    #: edition stamp below: a key pydantic does not know is gone on the next
    #: round trip, and a temporal run would forget its own window.
    temporal: TemporalSpec = Field(default_factory=TemporalSpec)
    #: The domain edition that contributed vocabulary and source policy, and the
    #: version it declared. Declared on the model because pydantic drops keys it
    #: does not know: an edition stamped on the dict but not on the model would
    #: vanish on the first `WorkflowPlan(**plan).model_dump()` round trip, and a
    #: run would lose the record of what shaped it.
    edition: str = ""
    edition_version: str = ""
    #: Derived bucket rules contributed by the edition. Declared here for the
    #: same reason as the edition stamp: a key pydantic does not know is gone on
    #: the next round trip, and the bucket view would forget how to group.
    buckets: list[dict] = Field(default_factory=list)
    #: How the edition places a document in a tier, as data. Declared here for
    #: the same reason as the buckets: pydantic drops a key it does not know,
    #: and a run that lost the policy would file every document in one tier.
    tiering: dict[str, Any] = Field(default_factory=dict)


class SourceRef(BaseModel):
    url: str
    title: str = ""
    quote: str
    retrieved_at: str  # ISO-8601
    # Phase-1 evidence chain (all optional; pin the quote to preserved text):
    reference_id: str = ""  # <n> citation marker from Crawl4AI references
    start: int | None = None  # char offset of quote in the preserved source text
    end: int | None = None
    # The stored page this claim came from. `start`/`end` above are only
    # meaningful against text that still exists; this is how a reader gets to
    # it. Empty when the run stored no evidence (probe/tests, no repo method).
    page_id: str = ""
    content_hash: str = ""  # re-fetch and confirm the page has not changed


class ProvenanceField(BaseModel):
    """One stored cell: its value, whether the evidence supports it, and where it came from.

    The receipt fields are not decoration. `validator._receipt_for` builds them
    and `RecordRow.model_validate` is the last step before a row is stored — and
    pydantic drops any field this model does not declare. They were not declared,
    so every stored cell lost `read_method`, `confidence`, `certainty` and their
    labels, and the route that actually produced a value was invisible the moment
    it was written.

    That is the same failure as the run counters being dropped in `main._emit`: a
    value computed carefully, stored, and then removed by a model that did not
    declare it. It also made source reputation blind to method — there was no way
    to ask which routes produced the verified cells a host is credited with.

    `extra="allow"` rather than enumerating each field: the receipt is assembled
    by `confidence.receipt` in one place, and a model that had to be updated every
    time that function grew a key is the same trap wearing a different hat.
    """
    model_config = {"extra": "allow"}

    value: Any | None
    verification_status: Verification
    source: SourceRef
    #: The date the source says its content is from (the search engine's
    #: `published_date`, normalised), or empty when it said nothing. It is a
    #: sibling of `source` rather than a member of it because it describes the
    #: fact's validity, not the retrieval: a page fetched today can describe a
    #: fact from 2021, and collapsing the two is how a stale value starts to look
    #: fresh.
    valid_time: str = ""
    #: The requested window plus whether `valid_time` fell inside it. Purely
    #: advisory: it never changes `verification_status`. A date mismatch is a
    #: finding about relevance, not proof that the quote is not on the page.
    temporal_check: dict = Field(default_factory=dict)


class RunEvent(BaseModel):
    type: str
    run_id: str
    stage: str
    message: str
    progress: int = 0
    timestamp: str
    data: dict = {}
