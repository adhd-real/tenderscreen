"""Deterministic parsers for Indian money amounts and dates.

They never guess: text that could be read more than one way is rejected with a
reason instead of a value.
"""

from tenderscreen.parsing.dates import DateRejection, DateResult, parse_date
from tenderscreen.parsing.money import AmountRejection, AmountResult, parse_inr

__all__ = [
    "AmountRejection",
    "AmountResult",
    "DateRejection",
    "DateResult",
    "parse_date",
    "parse_inr",
]
