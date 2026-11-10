"""Temporal parsing: what a prompt asks about in time, and what a cell can say.

The interesting cases are the negative ones. A parser that turns every four-digit
number into an as-of date would silently discard good sources for a request like
"companies founded in 2020", so the false-positive guard is tested as hard as the
happy path.
"""
import datetime as dt

from app.services import temporal


TODAY = dt.date(2026, 11, 8)


# -- parse: nothing requested -------------------------------------------------

def test_empty_prompt_requests_no_time():
    spec = temporal.parse("", today=TODAY)
    assert spec.kind == "none"
    assert spec.phrase == ""


def test_a_bare_year_is_a_field_filter_not_an_as_of_date():
    # "founded in 2020" describes the records, not the state of the world.
    assert temporal.parse("companies founded in 2020", today=TODAY).kind == "none"
    assert temporal.parse("startups that launched in 2024", today=TODAY).kind == "none"


def test_an_ordinary_number_is_not_a_date():
    assert temporal.parse("top 10 stocks in india", today=TODAY).kind == "none"


# -- parse: points ------------------------------------------------------------

def test_iso_as_of_is_a_point():
    spec = temporal.parse("top 10 stocks as of 2024-09-29", today=TODAY)
    assert spec.kind == "point"
    assert spec.as_of == "2024-09-29"
    assert "as of 2024-09-29" in spec.phrase


def test_day_first_date_is_read_internationally():
    spec = temporal.parse("holdings as of 29-09-2026", today=TODAY)
    assert spec.kind == "point"
    assert spec.as_of == "2026-09-29"


def test_as_of_today_is_current():
    spec = temporal.parse("who leads the market as of today?", today=TODAY)
    assert spec.kind == "current"
    assert spec.as_of == TODAY.isoformat()


def test_month_as_of_anchors_to_the_first_day():
    spec = temporal.parse("directory as of March 2024", today=TODAY)
    assert spec.kind == "point"
    assert spec.as_of == "2024-03-01"


def test_as_of_a_year_anchors_to_the_end_of_that_year():
    spec = temporal.parse("the market as of 2023", today=TODAY)
    assert spec.kind == "point"
    assert spec.as_of == "2023-12-31"


def test_current_is_a_point_at_the_run_date():
    spec = temporal.parse("current ceos of the ftse 100", today=TODAY)
    assert spec.kind == "current"
    assert spec.as_of == TODAY.isoformat()


def test_latest_also_means_current():
    assert temporal.parse("latest funding rounds", today=TODAY).kind == "current"


# -- parse: windows -----------------------------------------------------------

def test_between_years_covers_both_whole_years():
    spec = temporal.parse("funding between 2020 and 2023", today=TODAY)
    assert spec.kind == "window"
    assert spec.start == "2020-01-01"
    assert spec.end == "2023-12-31"


def test_from_month_to_month_is_a_window():
    spec = temporal.parse("deals from March 2024 to June 2024", today=TODAY)
    assert spec.kind == "window"
    assert spec.start == "2024-03-01"
    assert spec.end == "2024-06-30"


def test_since_runs_to_today():
    spec = temporal.parse("regulations since 2021", today=TODAY)
    assert spec.kind == "window"
    assert spec.start == "2021-01-01"
    assert spec.end == TODAY.isoformat()


def test_a_written_date_inside_a_window_keeps_its_year():
    """The comma in `May 1, 2024` used to end the bound, so the window read
    `May 1` .. `June 1` — no year in either, and the whole request silently
    became 'no time requested'."""
    spec = temporal.parse("news between May 1, 2024 and June 1, 2024", today=TODAY)
    assert spec.kind == "window"
    assert (spec.start, spec.end) == ("2024-05-01", "2024-06-01")

    since = temporal.parse("since 26 September 2025", today=TODAY)
    assert since.kind == "window" and since.start == "2025-09-26"

    frm = temporal.parse("deals from 26 September 2025 to 1 October 2025", today=TODAY)
    assert (frm.start, frm.end) == ("2025-09-26", "2025-10-01")


def test_last_six_months_ends_today():
    spec = temporal.parse("announcements in the last 6 months", today=TODAY)
    assert spec.kind == "window"
    assert spec.end == TODAY.isoformat()
    assert spec.start == "2026-05-08"


def test_this_year_is_the_calendar_year():
    spec = temporal.parse("this year's filings", today=TODAY)
    assert spec.kind == "window"
    assert spec.start == "2026-01-01"
    assert spec.end == "2026-12-31"


def test_quarter_is_its_three_months():
    spec = temporal.parse("results for Q3 2024", today=TODAY)
    assert spec.kind == "window"
    assert spec.start == "2024-07-01"
    assert spec.end == "2024-09-30"


def test_half_year_is_six_months():
    spec = temporal.parse("budget for H2 2024", today=TODAY)
    assert spec.kind == "window"
    assert spec.start == "2024-07-01"
    assert spec.end == "2024-12-31"


def test_reversed_window_is_normalised():
    spec = temporal.parse("between 2023 and 2020", today=TODAY)
    assert spec.kind == "window"
    assert spec.start < spec.end


# -- parse_date / valid_interval ---------------------------------------------

def test_rfc_2822_published_date_is_read():
    assert temporal.parse_date("Tue, 03 Sep 2024 10:00:00 GMT") == dt.date(2024, 9, 3)


def test_iso_datetime_published_date_is_read():
    assert temporal.parse_date("2024-09-03T10:00:00Z") == dt.date(2024, 9, 3)


def test_empty_or_garbage_date_is_none():
    assert temporal.parse_date("") is None
    assert temporal.parse_date("not a date") is None


def test_normalize_date_makes_every_provider_shape_iso():
    assert temporal.normalize_date("2024-09-03T10:00:00Z") == "2024-09-03"
    assert temporal.normalize_date("Tue, 11 Mar 2025 17:00:00 GMT") == "2025-03-11"
    assert temporal.normalize_date("2024") == "2024-01-01"
    # Unreadable text is kept rather than silently blanked: an odd date is still
    # evidence, and `check` will report it as unknown.
    assert temporal.normalize_date("sometime in spring") == "sometime in spring"


def test_a_written_date_is_not_reduced_to_january_first():
    """Measured live: a page said `September 26, 2025` and the cell's date came
    back `2025-01-01`, because only the bare year was read. A wrong date is
    worse than a missing one — it passes every window check for the year."""
    assert temporal.normalize_date("September 26, 2025") == "2025-09-26"
    assert temporal.normalize_date("Sept 26, 2025") == "2025-09-26"
    assert temporal.normalize_date("26 September 2025") == "2025-09-26"
    assert temporal.normalize_date("26th September 2025") == "2025-09-26"
    assert temporal.normalize_date("May 5,2024") == "2024-05-05"
    assert temporal.parse_date("September 26, 2025") == dt.date(2025, 9, 26)


def test_a_slashed_iso_date_keeps_its_month():
    assert temporal.normalize_date("2025/09/26") == "2025-09-26"
    assert temporal.normalize_date("2024-06") == "2024-06-01"
    assert temporal.valid_interval("2024-06") == (
        dt.date(2024, 6, 1), dt.date(2024, 6, 30), "month")
    # A requested window written as months covers those months, not January.
    assert temporal._window_bounds("2024-06", "2024-09") == ("2024-06-01", "2024-09-30")


def test_an_impossible_day_is_unknown_not_clamped():
    """`February 31` is not a date. Clamping it invents a day the text never
    claimed; `check` reports the absence as `unknown` instead."""
    assert temporal.parse_date("February 31, 2025") is None
    assert temporal.valid_interval("February 31, 2025") is None


def test_year_precision_covers_the_whole_year():
    assert temporal.valid_interval("2024") == (
        dt.date(2024, 1, 1), dt.date(2024, 12, 31), "year")


def test_month_precision_covers_the_whole_month():
    assert temporal.valid_interval("2024-02") == (
        dt.date(2024, 2, 1), dt.date(2024, 2, 29), "month")


def test_day_precision_is_one_day():
    assert temporal.valid_interval("2024-02-29") == (
        dt.date(2024, 2, 29), dt.date(2024, 2, 29), "day")


# -- check: advisory only -----------------------------------------------------

def test_no_requested_time_produces_no_check():
    assert temporal.check("2024-01-01", temporal.parse("", today=TODAY)) == {}


def test_missing_date_is_unknown_not_outside():
    # Absence of a date is not evidence of the wrong date.
    out = temporal.check("", temporal.parse("as of 2024-09-29", today=TODAY))
    assert out["status"] == "unknown"


def test_a_date_inside_the_window_is_within():
    spec = temporal.parse("between 2020 and 2023", today=TODAY)
    assert temporal.check("2021-06-01", spec)["status"] == "within"


def test_a_day_before_the_window_is_before():
    spec = temporal.parse("between 2020 and 2023", today=TODAY)
    assert temporal.check("2019-12-31", spec)["status"] == "before"


def test_a_day_after_the_window_is_after():
    spec = temporal.parse("between 2020 and 2023", today=TODAY)
    assert temporal.check("2024-01-01", spec)["status"] == "after"


def test_a_year_straddling_the_window_edge_is_uncertain():
    # 2023 is partly outside a request for "between 2020 and June 2023"; a
    # year-precision date cannot be confidently placed.
    spec = temporal.parse("between 2020 and June 2023", today=TODAY)
    assert temporal.check("2023", spec)["status"] == "uncertain"


def test_point_accepts_a_date_at_or_before_it():
    spec = temporal.parse("as of 2024-09-29", today=TODAY)
    assert temporal.check("2024-09-29", spec)["status"] == "within"
    assert temporal.check("2024-09-01", spec)["status"] == "within"


def test_point_rejects_a_date_after_it():
    spec = temporal.parse("as of 2024-09-29", today=TODAY)
    assert temporal.check("2024-10-01", spec)["status"] == "after"


def test_check_carries_the_requested_window_for_the_reader():
    spec = temporal.parse("between 2020 and 2023", today=TODAY)
    out = temporal.check("2021-06-01", spec)
    assert out["start"] == "2020-01-01"
    assert out["end"] == "2023-12-31"
    assert out["valid_time"] == "2021-06-01"
    assert out["precision"] == "day"


# -- search shaping -----------------------------------------------------------

def test_window_becomes_a_search_date_range():
    spec = temporal.parse("between 2020 and 2023", today=TODAY)
    assert temporal.search_window(spec) == ("2020-01-01", "2023-12-31")


def test_point_only_sets_an_end_date():
    # A point asks for the world as it was on a date; the engine can be told not
    # to return later material without being told it must be new.
    spec = temporal.parse("as of 2024-09-29", today=TODAY)
    assert temporal.search_window(spec) == ("", "2024-09-29")


def test_current_does_not_invent_a_window():
    spec = temporal.parse("current ceos", today=TODAY)
    assert temporal.search_window(spec) == ("", "")


def test_none_does_not_invent_a_window():
    assert temporal.search_window(temporal.parse("", today=TODAY)) == ("", "")


def test_summary_names_the_window():
    assert temporal.summary(temporal.parse("between 2020 and 2023",
                                          today=TODAY)) == "2020-01-01 to 2023-12-31"
    assert temporal.summary(temporal.parse("as of 2024-09-29",
                                          today=TODAY)) == "as of 2024-09-29"


# -- robustness ---------------------------------------------------------------

def test_parse_never_raises_on_a_giant_prompt():
    assert temporal.parse("as of " + "x" * 50000, today=TODAY).kind == "none"


def test_check_accepts_a_plan_dict_as_well_as_a_spec():
    spec = temporal.parse("between 2020 and 2023", today=TODAY).model_dump()
    assert temporal.check("2021-01-01", spec)["status"] == "within"


def test_check_tolerates_a_malformed_spec_dict():
    assert temporal.check("2021-01-01", {"kind": "nonsense"}) == {}
