"""Value comparison is where `changed` is won or lost.

Each case here is a real formatting difference that would produce a wrong
verdict under string equality.
"""
from __future__ import annotations

import pytest

from app.verify.value_normalizer import compare, normalize_value


@pytest.mark.parametrize("left,right", [
    ("$99/month", "99 USD per month"),
    ("$1,299/year", "1299 USD per year"),
    ("$99/month", "$99.00 per month"),
    ("10 GB", "10,000 MB"),
    ("v2.1.0", "2.1.0"),
    ("1,200", "1200"),
    ("30 days", "30 day"),
])
def test_equivalent_formatting(left, right):
    result = compare(left, right)
    assert result.equivalent, f"{left!r} vs {right!r}: {result.reason}"


@pytest.mark.parametrize("left,right,reason", [
    ("$99/month", "$129/month", "different_amount"),
    ("$99/month", "$99/month per seat", "different_basis"),
    ("$99/month", "$99/year", "different_period"),
    ("10%", "10 percentage points", "percent_vs_points"),
    ("v2.1.0", "v2.2.0", "different_version"),
    ("10 GB", "10 GiB", "different_amount"),
    ("99", "$99", "type_mismatch"),
])
def test_not_equivalent(left, right, reason):
    result = compare(left, right)
    assert not result.equivalent
    assert result.reason == reason, f"{left!r} vs {right!r}: {result.reason}"


def test_ambiguous_currency_is_not_silently_converted():
    result = compare("$99/month", "99 EUR per month")
    assert not result.equivalent
    assert result.reason == "ambiguous_currency"


@pytest.mark.parametrize("left,right,reason", [
    # A different currency and a different amount is a scope difference, not a
    # replacement value: the amounts are not comparable, so the finding is the
    # currency. Reporting `different_amount` here once produced a false `changed`.
    ("US$99/month", "€129/month", "different_currency"),
    # `$` alone is ambiguous, so the same comparison cannot claim to know which
    # currency moved — it is a scope block either way.
    ("$99/month", "€129/month", "ambiguous_currency"),
    # Same currency, different billing period: also not comparable as amounts.
    ("$99/month", "$129/year", "different_period"),
])
def test_scope_is_decided_before_amounts(left, right, reason):
    result = compare(left, right)
    assert not result.equivalent
    assert result.reason == reason, f"{left!r} vs {right!r}: {result.reason}"


def test_a_dollar_sign_with_the_same_code_is_accepted():
    """`$` is ambiguous in general, but not when both sides say USD."""
    assert compare("$99/month", "99 USD per month").equivalent


def test_normalize_shape_is_stable_for_the_ui():
    money = normalize_value("$1,299 / year per seat")
    assert money["type"] == "money"
    assert money["amount"] == 1299.0
    assert money["currency"] == "USD"
    assert money["period"] == "year"
    assert money["per_unit"] == "seat"


def test_a_per_unit_price_parses_in_the_order_people_write_it():
    """"per member per month" is two qualifiers, not an unparseable tail.

    It used to fail because the tail was split on the literal word `per`, so the
    value stayed a string. A string claim value cannot use the currency/period
    scope checks, which is the whole reason `changed` is trustworthy for prices.
    """
    money = normalize_value("$10 per member per month")
    assert money["type"] == "money", money
    assert money["amount"] == 10.0
    assert money["period"] == "month"
    assert money["per_unit"] == "member"

    # The same fact, written three ways.
    for variant in ("$10 per member/month", "10 USD per member per month",
                    "$10/member/month"):
        assert compare("$10 per member per month", variant).equivalent, variant


def test_a_billing_note_does_not_overwrite_the_price_period():
    money = normalize_value("$10 per member per month (billed annually)")
    assert money["period"] == "month", money
    assert money["per_unit"] == "member"


def test_unparseable_values_fall_back_to_text_not_to_an_error():
    assert normalize_value("Acme Corp")["type"] == "string"
    assert compare("Acme Corp", "acme  corp.").equivalent
    assert not compare("Acme Corp", "Beta Inc").equivalent


def test_units_are_compared_in_a_base_unit():
    assert compare("1 TB", "1000 GB").equivalent
    assert compare("10 gb", "10,000 mb").equivalent
