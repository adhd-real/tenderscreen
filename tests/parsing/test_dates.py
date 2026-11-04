import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone

import pytest

from tenderscreen.ist import IST
from tenderscreen.parsing.dates import (
    _TIME_PART,
    MAX_TEXT_CHARS,
    DateRejection,
    DateResult,
    _clock,
    _reject,
    _zone,
    parse_date,
)


def ist(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=IST)


# The five formats named in the task, verbatim.
REQUIRED_CASES = [
    ("15-Oct-2026 05:00 PM", ist(2026, 10, 15, 17, 0), True),
    ("15-Oct-2026", ist(2026, 10, 15), False),
    ("15/10/2026", ist(2026, 10, 15), False),
    ("15.10.2026", ist(2026, 10, 15), False),
    ("2026-10-15", ist(2026, 10, 15), False),
]


@pytest.mark.parametrize(("text", "expected", "has_time"), REQUIRED_CASES)
def test_required_formats(text: str, expected: datetime, has_time: bool) -> None:
    result = parse_date(text)
    assert result == DateResult(value=expected, reason=None, has_time=has_time)
    assert result.ok


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Day first, always: 05/10 is 5 October, never 10 May
        ("05/10/2026", ist(2026, 10, 5)),
        ("5/1/2026", ist(2026, 1, 5)),
        ("15-10-2026", ist(2026, 10, 15)),
        ("15 Oct 2026", ist(2026, 10, 15)),
        ("15 October 2026", ist(2026, 10, 15)),
        ("15-OCT-2026", ist(2026, 10, 15)),
        ("15th October, 2026", ist(2026, 10, 15)),
        ("1st Sept 2026", ist(2026, 9, 1)),
        ("29/02/2028", ist(2028, 2, 29)),
        ("01/01/1950", ist(1950, 1, 1)),
        ("15 अक्टूबर 2026", ist(2026, 10, 15)),
        ("15 फ़रवरी 2026", ist(2026, 2, 15)),
        ("15 फरवरी 2026", ist(2026, 2, 15)),
    ],
)
def test_date_only_variants(text: str, expected: datetime) -> None:
    assert parse_date(text) == DateResult(value=expected, reason=None, has_time=False)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("15-Oct-2026 5:00 pm", ist(2026, 10, 15, 17, 0)),
        ("15-Oct-2026 05:00PM", ist(2026, 10, 15, 17, 0)),
        ("15-Oct-2026 05.00 P.M.", ist(2026, 10, 15, 17, 0)),
        ("15-Oct-2026 12:00 PM", ist(2026, 10, 15, 12, 0)),
        ("15-Oct-2026 12:30 AM", ist(2026, 10, 15, 0, 30)),
        ("15/10/2026 17:00", ist(2026, 10, 15, 17, 0)),
        ("15/10/2026 17:00:45", ist(2026, 10, 15, 17, 0, 45)),
        ("15.10.2026 at 17:00 Hrs", ist(2026, 10, 15, 17, 0)),
        ("15.10.2026 at 1700 hrs", ist(2026, 10, 15, 17, 0)),
        ("15.10.2026, 11:00 AM IST", ist(2026, 10, 15, 11, 0)),
        ("15-Oct-2026 3 PM", ist(2026, 10, 15, 15, 0)),
        ("2026-10-15 17:00", ist(2026, 10, 15, 17, 0)),
        ("2026-10-15T17:00:00", ist(2026, 10, 15, 17, 0)),
        ("2026-10-15T17:00:00+05:30", ist(2026, 10, 15, 17, 0)),
        # An explicit UTC offset is unambiguous: converted to IST
        ("2026-10-15T11:30:00Z", ist(2026, 10, 15, 17, 0)),
        ("2026-10-15T11:30:00+00:00", ist(2026, 10, 15, 17, 0)),
        ("2026-10-15T12:30:00-0100", ist(2026, 10, 15, 19, 0)),
        ("15/10/2026 17:00 +0530", ist(2026, 10, 15, 17, 0)),
        ("15 Sept, 2026 10.30 a.m.", ist(2026, 9, 15, 10, 30)),
        ("31-12-2026 23:59:59", ist(2026, 12, 31, 23, 59, 59)),
        ("15/10/2026 00:00", ist(2026, 10, 15, 0, 0)),
        ("15/10/2026 17:00 +05:30 IST", ist(2026, 10, 15, 17, 0)),
        ("01/01/2100 00:00", ist(2100, 1, 1)),
    ],
)
def test_date_and_time_variants(text: str, expected: datetime) -> None:
    result = parse_date(text)
    assert result == DateResult(value=expected, reason=None, has_time=True)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", DateRejection.EMPTY),
        ("  ", DateRejection.EMPTY),
        ("1" * (MAX_TEXT_CHARS + 1), DateRejection.TOO_LONG),
        # Impossible dates
        ("31/02/2026", DateRejection.IMPOSSIBLE_DATE),
        ("29/02/2026", DateRejection.IMPOSSIBLE_DATE),
        ("31-Apr-2026", DateRejection.IMPOSSIBLE_DATE),
        ("00/10/2026", DateRejection.IMPOSSIBLE_DATE),
        ("15/13/2026", DateRejection.IMPOSSIBLE_DATE),
        ("2026-02-30", DateRejection.IMPOSSIBLE_DATE),
        # Impossible times
        ("15/10/2026 24:00", DateRejection.IMPOSSIBLE_TIME),
        ("15/10/2026 17:60", DateRejection.IMPOSSIBLE_TIME),
        ("15/10/2026 13:00 PM", DateRejection.IMPOSSIBLE_TIME),
        ("15/10/2026 00:30 AM", DateRejection.IMPOSSIBLE_TIME),
        # Ambiguous or unsupported
        ("15/10/26", DateRejection.TWO_DIGIT_YEAR),
        ("15-Oct-26", DateRejection.TWO_DIGIT_YEAR),
        ("15-Foo-2026", DateRejection.UNKNOWN_MONTH),
        ("Oct 15, 2026", DateRejection.UNRECOGNISED),
        ("2026/10/15", DateRejection.UNRECOGNISED),
        ("15/10-2026", DateRejection.UNRECOGNISED),
        ("15/10/2026 and 16/10/2026", DateRejection.UNRECOGNISED),
        ("tomorrow", DateRejection.UNRECOGNISED),
        ("15/10/2026 17:00 GMT", DateRejection.UNRECOGNISED),
        ("2026-10-15T17:00:00+25:00", DateRejection.IMPOSSIBLE_TIME),
        ("2026-10-15T17:00:00+05:60", DateRejection.IMPOSSIBLE_TIME),
        ("15/10/202", DateRejection.UNRECOGNISED),
        ("15/10/20266", DateRejection.UNRECOGNISED),
        ("15/10/2026 5", DateRejection.UNRECOGNISED),
        ("15-Oct-2026 0500 PM", DateRejection.UNRECOGNISED),
        ("Last date: 15/10/2026", DateRejection.UNRECOGNISED),
        # Found in review
        ("9999-12-31T23:00:00Z", DateRejection.YEAR_OUT_OF_RANGE),
        ("0001-01-01T00:00:00+05:30", DateRejection.YEAR_OUT_OF_RANGE),
        ("15/10/1000", DateRejection.YEAR_OUT_OF_RANGE),
        ("15/10/1949", DateRejection.YEAR_OUT_OF_RANGE),
        ("15/10/2101", DateRejection.YEAR_OUT_OF_RANGE),
        ("15/10/2026 17:00 +0000 IST", DateRejection.CONFLICTING_TIME_ZONE),
        ("2026-10-15T17:00:00Z IST", DateRejection.CONFLICTING_TIME_ZONE),
        ("2026-10-1517:00", DateRejection.UNRECOGNISED),
        ("15/10/2026\u00b2", DateRejection.UNRECOGNISED),
        ("15 \u0985\u0995\u09cd\u099f\u09cb\u09ac\u09b0 2026", DateRejection.UNRECOGNISED),
    ],
)
def test_rejections_carry_a_reason(text: str, reason: DateRejection) -> None:
    result = parse_date(text)
    assert result == DateResult(value=None, reason=reason, has_time=False)
    assert not result.ok


def test_result_is_aware_and_in_kolkata() -> None:
    value = parse_date("15-Oct-2026 05:00 PM").value
    assert value is not None
    assert value.tzinfo is IST
    assert value.utcoffset() == timedelta(hours=5, minutes=30)
    assert value.astimezone(UTC) == datetime(2026, 10, 15, 11, 30, tzinfo=UTC)


def test_result_requires_exactly_one_of_value_and_reason() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DateResult(value=None, reason=None)
    with pytest.raises(ValueError, match="exactly one"):
        DateResult(value=ist(2026, 1, 1), reason=DateRejection.EMPTY)


def test_result_rejects_naive_or_non_ist_values() -> None:
    with pytest.raises(ValueError, match="Asia/Kolkata"):
        DateResult(value=datetime(2026, 1, 1), reason=None)  # noqa: DTZ001 - testing the guard
    with pytest.raises(ValueError, match="Asia/Kolkata"):
        DateResult(value=datetime(2026, 1, 1, tzinfo=UTC), reason=None)


def test_rejection_has_no_time() -> None:
    with pytest.raises(ValueError, match="has_time"):
        DateResult(value=None, reason=DateRejection.EMPTY, has_time=True)


def test_works_without_system_timezone_files() -> None:
    """A fresh interpreter whose tz search path is empty must still get IST (from tzdata)."""
    code = (
        "from tenderscreen.parsing.dates import parse_date;"
        "v = parse_date('15/10/2026 17:00').value;"
        "print(v.utcoffset(), v.tzinfo)"
    )
    env = {**os.environ, "PYTHONTZPATH": "/nonexistent-tz-path"}
    out = subprocess.run(  # noqa: S603 - fixed command, no untrusted input
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "5:30:00 Asia/Kolkata"


# Private helpers (CLAUDE.md rule 6: every function gets its own tests).


def time_match(text: str) -> re.Match[str]:
    match = _TIME_PART.fullmatch(text)
    assert match is not None
    return match


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (" 05:00 pm", (17, 0, 0)),
        (" 12:00 am", (0, 0, 0)),
        (" 12:15 pm", (12, 15, 0)),
        (" 23:59:59", (23, 59, 59)),
        (" 1700 hrs", (17, 0, 0)),
        (" 3 pm", (15, 0, 0)),
        (" 13:00 pm", None),
        (" 0:30 am", None),
        (" 24:00", None),
        (" 2360 hrs", None),
        (" 10:00:60", None),
    ],
)
def test_clock(text: str, expected: tuple[int, int, int] | None) -> None:
    assert _clock(time_match(text)) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (" 17:00", IST),
        (" 17:00 ist", IST),
        ("t17:00z", UTC),
        ("t17:00+05:30 ist", timezone(timedelta(hours=5, minutes=30))),
        ("t17:00-01:00", timezone(timedelta(hours=-1))),
        ("t17:00+24:00", DateRejection.IMPOSSIBLE_TIME),
        ("t17:00+05:61", DateRejection.IMPOSSIBLE_TIME),
        ("t17:00z ist", DateRejection.CONFLICTING_TIME_ZONE),
        (" 17:00 +0100 (ist)", DateRejection.CONFLICTING_TIME_ZONE),
    ],
)
def test_zone(text: str, expected: object) -> None:
    assert _zone(time_match(text)) == expected


def test_reject_builds_a_rejection() -> None:
    assert _reject(DateRejection.EMPTY) == DateResult(None, DateRejection.EMPTY)
