"""India Standard Time: the zone every tender deadline and timestamp is kept in.

``zoneinfo`` reads the system's tz database when one exists and otherwise falls
back to the ``tzdata`` package (a declared dependency), so this works on minimal
containers and Windows without system timezone files.
"""

from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

IST: Final = ZoneInfo("Asia/Kolkata")


def to_ist(moment: datetime) -> datetime:
    """Return the same instant in IST. Naive datetimes are refused, never assumed."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"naive datetime {moment.isoformat()} has no time zone; refusing to guess")
    return moment.astimezone(IST)


def now_ist() -> datetime:
    """The current time in IST."""
    return datetime.now(IST)
