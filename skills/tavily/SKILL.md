# Tavily — DATAGOBLIN discovery backend

Role: broad web discovery only (a handful of queries per run, `max_results` 5,
then dedupe URLs for crawl). Not extraction: a synthesised answer is not a
locatable quote, so `include_answer` / `get_search_context` / `qna_search` are
not used.

Verified 2026-11-08 against docs.tavily.com (api-reference/endpoint/search,
api-credits) and the installed `tavily-python` client
(`backend/.venv/Lib/site-packages/tavily/`).

## Shape we actually call

```python
from tavily import TavilyClient
TavilyClient(api_key=key).search(
    query,
    search_depth="basic",     # basic|advanced|fast|ultra-fast
    max_results=5,            # 0–20, default 10
    topic="general",          # general|news|finance
    include_published_date=True,  # opt-in; null when no date is detectable
    # start_date="YYYY-MM-DD", end_date="YYYY-MM-DD",  # only when a window was asked for
    include_domains=[...],    # <= 300
    exclude_domains=[...],    # <= 150, never together with include_domains
    include_answer=False,
    include_usage=True,       # per-call receipt: usage.credits + request_id
)
```

`include_domains` and `exclude_domains` are not accepted together. The adapter
sends the allowlist when both exist and applies the block list to the returned
results (`tavily._blocked`, subdomains included); sending both is a 400, which is
classified fatal and would end discovery for the whole run.

`filter_by_published_date` stays **false**. It also removes undated results, and
an undated source is `unknown`, not out-of-window. The date bounds shape the
search; they do not decide what may be cited.

`backend/app/providers/search/tavily.py` carries `score` (engine relevance,
0–1), `published_date` and result `id` out of the response. `score` is used as a
*bounded* ranking prior (`agents/graph.py`: weight 0.75, below one field hit), so
it can never outrank a candidate that names the entity.

## Credits

- 1,000 free credits/month, no card. Pay-as-you-go $0.008/credit. Plans
  $0.005–$0.0075/credit at volume. Reset on the first of the month.
- `basic` / `fast` / `ultra-fast` = 1 credit; `advanced` = 2.
- `extract` = 1 credit per 5 URLs; `map` = 1 credit per 10 pages. Neither is
  wired into this app yet, so neither is billed yet.

## Errors — classify by type, not by string

`tavily/errors.py` publishes `InvalidAPIKeyError`, `ForbiddenError`,
`UsageLimitExceededError` (and `TavilyKeylessLimitError`), `BadRequestError`.
The adapter treats all of them as **fatal** (`E_PROVIDER_FATAL`): retrying a bad
key, a forbidden plan or an exhausted quota cannot succeed, and the old
message-string match retried 432/433 and burned the discovery wall clock.

HTTP 429 / "rate limit" stays **transient** (`E_PROVIDER_TRANSIENT`), and
`Retry-After` is read from `retry_after_seconds`, a `response.headers` value, or
the message, and returned in `details.retry_after`.

## Parameters we have not adopted

- `time_range` (`day|week|month|year`): the plan carries explicit `start_date` /
  `end_date`, which is a narrower statement than "the last month" and does not
  silently include or exclude the boundaries.
- `exact_match`: quoted-phrase search; only worth it with an unquoted retry when
  it returns nothing.
- `auto_parameters`: can silently select `advanced` (2 credits).
- `include_domains_mode="prefer"`: turns a hard filter into a hint silently.
- `include_raw_content` as page evidence: bypasses the reducer, so offsets would
  index text the app never stored.
- `/crawl` replacing local traversal: loses the per-attempt outcome fields
  (`render_attempted`, `final_content_source`, …) that the no-silent-failure
  rule requires.
- `/research`: 4–250 credits and no span provenance.

Sources: docs.tavily.com api-reference/endpoint/search + api-credits;
github.com/tavily-ai/tavily-python.
