from datetime import UTC, datetime, timedelta, timezone

import pytest

from tenderscreen.ist import IST, now_ist, to_ist


def test_ist_is_kolkata_plus_0530() -> None:
    assert str(IST) == "Asia/Kolkata"
    assert datetime(2026, 1, 1, tzinfo=IST).utcoffset() == timedelta(hours=5, minutes=30)


def test_to_ist_converts_aware_datetimes() -> None:
    utc_noon = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    converted = to_ist(utc_noon)
    assert converted == utc_noon
    assert converted.tzinfo is IST
    assert (converted.hour, converted.minute) == (17, 30)


def test_to_ist_keeps_ist_values() -> None:
    value = datetime(2026, 10, 15, 17, 0, tzinfo=IST)
    assert to_ist(value) == value


def test_to_ist_refuses_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_ist(datetime(2026, 10, 15, 17, 0))  # noqa: DTZ001 - testing the guard


def test_to_ist_handles_fixed_offsets() -> None:
    plus_one = timezone(timedelta(hours=1))
    assert to_ist(datetime(2026, 1, 1, 0, 0, tzinfo=plus_one)).hour == 4


def test_now_ist_is_aware_and_ist() -> None:
    now = now_ist()
    assert now.tzinfo is IST
    assert abs(now - datetime.now(UTC)) < timedelta(minutes=1)
