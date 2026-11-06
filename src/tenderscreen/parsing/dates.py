"""Parse an Indian (day-first) date, with optional time, into an aware IST datetime.

``parse_date("15-Oct-2026 05:00 PM")`` gives 2026-10-15 17:00 Asia/Kolkata.

Accepted forms: ``15-Oct-2026``, ``15 October 2026``, ``15th Oct, 2026``,
``15 अक्टूबर 2026``, ``15/10/2026``, ``15.10.2026``, ``15-10-2026`` (always day
first), and ISO ``2026-10-15``; each optionally followed by a time such as
``05:00 PM``, ``17:00``, ``17:00:45``, ``05.00 p.m.``, ``3 PM``, ``1700 hrs``,
an ``IST`` suffix, or (ISO style) ``T17:00:00+05:30``.

It never guesses: two-digit years, month-first dates, bare hours without
minutes or AM/PM, other time-zone names, and impossible dates or times are
rejected with a reason. A date without a time is midnight IST with
``has_time=False``, so callers can tell "15 Oct" from "15 Oct 00:00".
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from tenderscreen.ist import IST
from tenderscreen.parsing.text import (
    DEVANAGARI,
    has_compatibility_digits,
    has_foreign_script,
    normalise_text,
)

MAX_TEXT_CHARS: Final = 100
# Tender and award dates fall well inside this range. Outside it a year is most
# likely a typo, and very old dates carry pre-1946 local-time offsets.
MIN_YEAR: Final = 1950
MAX_YEAR: Final = 2100


class DateRejection(StrEnum):
    """Why a text was not turned into a date. The value is a readable reason."""

    EMPTY = "empty text"
    TOO_LONG = f"text longer than {MAX_TEXT_CHARS} characters"
    UNRECOGNISED = "not a recognised day-first Indian date (and optional time)"
    TWO_DIGIT_YEAR = "two-digit year, so the century is ambiguous"
    UNKNOWN_MONTH = "unknown month name"
    YEAR_OUT_OF_RANGE = f"year outside {MIN_YEAR}-{MAX_YEAR}, probably a typo"
    IMPOSSIBLE_DATE = "no such calendar date"
    IMPOSSIBLE_TIME = "no such time of day or UTC offset"
    CONFLICTING_TIME_ZONE = "says IST but gives a different UTC offset"


@dataclass(frozen=True, slots=True)
class DateResult:
    """Either ``value`` (aware, in Asia/Kolkata) or ``reason``; never both."""

    value: datetime | None
    reason: DateRejection | None
    has_time: bool = False

    def __post_init__(self) -> None:
        if (self.value is None) == (self.reason is None):
            raise ValueError("DateResult needs exactly one of value and reason")
        if self.value is not None and not (
            isinstance(self.value.tzinfo, ZoneInfo) and self.value.tzinfo.key == "Asia/Kolkata"
        ):
            raise ValueError("DateResult value must be timezone-aware in Asia/Kolkata")
        if self.value is None and self.has_time:
            raise ValueError("a rejected DateResult cannot have has_time set")

    @property
    def ok(self) -> bool:
        return self.value is not None


_MONTH_NAMES: Final = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    "जनवरी": 1, "फ़रवरी": 2, "फरवरी": 2, "मार्च": 3, "अप्रैल": 4, "अप्रेल": 4,
    "मई": 5, "जून": 6, "जुलाई": 7, "अगस्त": 8, "सितंबर": 9, "सितम्बर": 9,
    "अक्टूबर": 10, "अक्तूबर": 10, "नवंबर": 11, "नवम्बर": 11, "दिसंबर": 12, "दिसम्बर": 12,
}  # fmt: skip
_MONTHS: Final = {normalise_text(name): number for name, number in _MONTH_NAMES.items()}

# Date patterns, tried in order against the start of the normalised text.
_ISO_DATE: Final = re.compile(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})")
_NUMERIC_DATE: Final = re.compile(
    r"(?P<day>\d{1,2})(?P<sep>[-/.])(?P<month>\d{1,2})(?P=sep)(?P<year>\d+)"
)
_NAMED_DATE: Final = re.compile(
    rf"(?P<day>\d{{1,2}})(?:st|nd|rd|th)?[-/. ]\s*(?P<month_name>[a-z{DEVANAGARI}]+)\.?"
    r"[-/., ]\s*(?P<year>\d+)"
)
_DATE_PATTERNS: Final = (_ISO_DATE, _NUMERIC_DATE, _NAMED_DATE)

_HOURS_WORD: Final = r"(?:hrs?\.?|hours)"
# Everything after the date: nothing, or a time with optional offset and suffixes.
_TIME_PART: Final = re.compile(
    r"(?:"
    r"(?:\s*,\s*(?:at\s+)?|\s+(?:at\s+)?|t)"  # separator: space, comma, "at", or ISO "T"
    r"(?:"
    rf"(?P<hhmm>\d{{4}})\s*{_HOURS_WORD}"  # 1700 hrs
    r"|(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2})(?::(?P<second>\d{2}))?)?"
    r"\s*(?P<ampm>[ap]\.?m\.?)?"
    r")"
    r"(?:\s*(?P<offset>z|(?P<offset_sign>[+-])(?P<offset_hours>\d{2}):?(?P<offset_minutes>\d{2})))?"
    rf"(?:\s*{_HOURS_WORD})?"
    r"(?:\s*(?P<ist>\(?ist\)?))?"
    r")?"
)


def _reject(reason: DateRejection) -> DateResult:
    return DateResult(value=None, reason=reason)


def _clock(time: re.Match[str]) -> tuple[int, int, int] | None:
    """(hour, minute, second) on the 24-hour clock, or None if impossible."""
    if time.group("hhmm"):
        hour, minute, second = int(time.group("hhmm")[:2]), int(time.group("hhmm")[2:]), 0
    else:
        hour = int(time.group("hour"))
        minute = int(time.group("minute") or 0)
        second = int(time.group("second") or 0)
        ampm = time.group("ampm")
        if ampm:
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if ampm.startswith("p") else 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour, minute, second


def _zone(time: re.Match[str]) -> tzinfo | DateRejection:
    """IST, or the explicit UTC offset ("z", "+05:30"), or why the zone is unusable."""
    if time.group("offset") is None:
        return IST
    if time.group("offset") == "z":
        zone: tzinfo = UTC
        delta = timedelta(0)
    else:
        hours, minutes = int(time.group("offset_hours")), int(time.group("offset_minutes"))
        if hours > 23 or minutes > 59:
            return DateRejection.IMPOSSIBLE_TIME
        delta = timedelta(hours=hours, minutes=minutes)
        if time.group("offset_sign") == "-":
            delta = -delta
        zone = timezone(delta)
    if time.group("ist") and delta != timedelta(hours=5, minutes=30):
        return DateRejection.CONFLICTING_TIME_ZONE
    return zone


def parse_date(text: str) -> DateResult:
    """Parse one day-first date (optionally with a time) into IST, or say why not."""
    if len(text) > MAX_TEXT_CHARS:
        return _reject(DateRejection.TOO_LONG)
    if has_compatibility_digits(text):
        return _reject(DateRejection.UNRECOGNISED)  # superscript or circled digits
    norm = normalise_text(text)
    if not norm:
        return _reject(DateRejection.EMPTY)
    if has_foreign_script(norm):
        return _reject(DateRejection.UNRECOGNISED)

    for pattern in _DATE_PATTERNS:
        found = pattern.match(norm)
        if found is not None:
            break
    else:
        return _reject(DateRejection.UNRECOGNISED)
    time = _TIME_PART.fullmatch(norm, found.end())
    if time is None:
        return _reject(DateRejection.UNRECOGNISED)
    has_time = time.group("hhmm") is not None or time.group("hour") is not None
    if time.group("hour") is not None and not (time.group("minute") or time.group("ampm")):
        return _reject(DateRejection.UNRECOGNISED)  # "15/10/2026 5": 5 what?

    year_text = found.group("year")
    if len(year_text) == 2:
        return _reject(DateRejection.TWO_DIGIT_YEAR)
    if len(year_text) != 4:
        return _reject(DateRejection.UNRECOGNISED)
    if not MIN_YEAR <= int(year_text) <= MAX_YEAR:
        return _reject(DateRejection.YEAR_OUT_OF_RANGE)
    if "month_name" in found.groupdict():
        month = _MONTHS.get(found.group("month_name"))
        if month is None:
            return _reject(DateRejection.UNKNOWN_MONTH)
    else:
        month = int(found.group("month"))
    try:
        day = date(int(year_text), month, int(found.group("day")))
    except ValueError:
        return _reject(DateRejection.IMPOSSIBLE_DATE)

    if not has_time:
        return DateResult(datetime(day.year, day.month, day.day, tzinfo=IST), None)
    clock = _clock(time)
    if clock is None:
        return _reject(DateRejection.IMPOSSIBLE_TIME)
    zone = _zone(time)
    if isinstance(zone, DateRejection):
        return _reject(zone)
    moment = datetime(day.year, day.month, day.day, *clock, tzinfo=zone)
    return DateResult(moment.astimezone(IST), None, has_time=True)
