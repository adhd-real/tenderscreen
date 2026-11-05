from datetime import datetime
from decimal import Decimal

import pytest

from tenderscreen.evals.fields import FieldKind, comparable, field_kind, normalise_text
from tenderscreen.ist import IST


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("contract_value_paise", FieldKind.MONEY),
        ("emd_paise", FieldKind.MONEY),
        ("award_date", FieldKind.DATE),
        ("bid_submission_deadline", FieldKind.DATE),
        ("published_at", FieldKind.DATE),
        ("number_of_bids", FieldKind.INTEGER),
        ("bid_count", FieldKind.INTEGER),
        ("pbg_percentage", FieldKind.DECIMAL),
        ("gst_percent", FieldKind.DECIMAL),
        ("successful_bidder_name", FieldKind.TEXT),
        ("contact_details", FieldKind.TEXT),
    ],
)
def test_field_kind_from_name(name: str, kind: FieldKind) -> None:
    assert field_kind(name) is kind


def test_normalise_text_ignores_spacing_and_case() -> None:
    assert normalise_text("  Synthetic   BUILDERS\nPvt Ltd ") == "synthetic builders pvt ltd"
    assert normalise_text("ड़") == normalise_text("ड़")  # canonical Unicode


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (FieldKind.MONEY, 2_500_000, 2_500_000),
        (FieldKind.MONEY, "Rs. 25,000", 2_500_000),
        (FieldKind.MONEY, "25 लाख", 250_000_000),
        (FieldKind.MONEY, 0, 0),
        (FieldKind.DATE, "15/10/2026", (datetime(2026, 10, 15, tzinfo=IST), False)),
        (FieldKind.DATE, "2026-10-15", (datetime(2026, 10, 15, tzinfo=IST), False)),
        (FieldKind.DATE, "15-Oct-2026 05:00 PM", (datetime(2026, 10, 15, 17, tzinfo=IST), True)),
        (FieldKind.INTEGER, 4, 4),
        (FieldKind.INTEGER, " 4 ", 4),
        (FieldKind.DECIMAL, "2.5", Decimal("2.5")),
        (FieldKind.DECIMAL, "2.50 %", Decimal("2.5")),
        (FieldKind.DECIMAL, Decimal("2.5"), Decimal("2.5")),
        (FieldKind.DECIMAL, 3, Decimal(3)),
        (FieldKind.TEXT, " Synthetic  Builders ", "synthetic builders"),
    ],
)
def test_comparable_values(kind: FieldKind, value: object, expected: object) -> None:
    assert comparable(kind, value) == expected


@pytest.mark.parametrize(
    ("kind", "value", "message"),
    [
        (FieldKind.MONEY, Decimal("2500.50"), "integer paise or text"),
        (FieldKind.MONEY, True, "integer paise or text"),
        (FieldKind.MONEY, -5, "negative"),
        (FieldKind.MONEY, "about five lakh", "not a money amount"),
        (FieldKind.MONEY, None, "integer paise or text"),
        (FieldKind.DATE, 20261015, "must be text"),
        (FieldKind.DATE, "31/02/2026", "not a date"),
        (FieldKind.INTEGER, "four", "whole number"),
        (FieldKind.INTEGER, False, "whole number"),
        (FieldKind.INTEGER, "१", "whole number"),  # Devanagari digit: say "1"
        (FieldKind.DECIMAL, "five", "not a number"),
        (FieldKind.DECIMAL, "NaN", "not a number"),
        (FieldKind.DECIMAL, [], "not a number"),
        (FieldKind.TEXT, "   ", "non-empty string"),
        (FieldKind.TEXT, 42, "non-empty string"),
    ],
)
def test_comparable_rejects_invalid_values(kind: FieldKind, value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        comparable(kind, value)


def test_money_written_two_ways_compares_equal() -> None:
    assert comparable(FieldKind.MONEY, "₹ 2,50,00,000") == comparable(FieldKind.MONEY, "2.5 Cr")
