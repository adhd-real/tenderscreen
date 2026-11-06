"""How each field's value is compared, decided by the field's name.

| Name pattern                                   | Kind    | Compared as                  |
|------------------------------------------------|---------|------------------------------|
| ends in ``_paise``                             | money   | integer paise, exactly       |
| ends in ``_date``, ``_deadline`` or ``_at``    | date    | IST datetime, exactly        |
| starts ``number_of_`` or ends ``_count``       | integer | exactly                      |
| ends ``_percentage`` or ``_percent``           | decimal | exactly (2.5 equals 2.50)    |
| anything else                                  | text    | whitespace and case ignored  |

Money may be written as integer paise (2500000) or as text parse_inr accepts
("Rs. 25,000"); dates as text parse_date accepts ("15/10/2026", "2026-10-15").
Floating-point numbers are refused for money (CLAUDE.md rule 1).
"""

import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, TypeGuard

from tenderscreen.parsing import parse_date, parse_inr


class FieldKind(StrEnum):
    MONEY = "money"
    DATE = "date"
    INTEGER = "integer"
    DECIMAL = "decimal"
    TEXT = "text"


# A comparable form of a value: equal keys mean equal answers.
type Comparable = int | Decimal | str | tuple[datetime, bool]


def field_kind(name: str) -> FieldKind:
    if name.endswith("_paise"):
        return FieldKind.MONEY
    if name.endswith(("_date", "_deadline", "_at")):
        return FieldKind.DATE
    if name.startswith("number_of_") or name.endswith("_count"):
        return FieldKind.INTEGER
    if name.endswith(("_percentage", "_percent")):
        return FieldKind.DECIMAL
    return FieldKind.TEXT


# Fields whose values may hold personal contact data: reports and error
# messages never show them (CLAUDE.md: never log phone numbers).
_PRIVATE_FIELD: Final = re.compile(r"contact|phone|mobile|email|e_mail|address", re.IGNORECASE)
# Zero-width space, non-joiner, joiner, word joiner, byte-order mark, soft hyphen:
# invisible in a PDF viewer, so two names that look identical must compare equal.
_INVISIBLE: Final = dict.fromkeys((0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD))


def is_private_field(name: str) -> bool:
    """True for fields that may hold names, phone numbers, emails or addresses."""
    return _PRIVATE_FIELD.search(name) is not None


def normalise_text(text: str) -> str:
    """Canonical Unicode, invisible characters dropped, whitespace collapsed, case folded."""
    visible = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    return " ".join(visible.split()).casefold()


def _is_int(value: object) -> TypeGuard[int]:
    """An int that is not a bool (JSON true/false must not count as 1/0)."""
    return isinstance(value, int) and not isinstance(value, bool)


def comparable(kind: FieldKind, value: object) -> Comparable:
    """The value in comparable form; ``ValueError`` (with a reason) if it is not valid."""
    if kind is FieldKind.MONEY:
        if _is_int(value):
            if value < 0:
                raise ValueError(f"money cannot be negative: {value!r}")
            return value
        if isinstance(value, str):
            result = parse_inr(value)
            if result.paise is None:
                raise ValueError(f"not a money amount ({result.reason}): {value!r}")
            return result.paise
        raise ValueError(f"money must be integer paise or text, not {value!r}")
    if kind is FieldKind.DATE:
        if not isinstance(value, str):
            raise ValueError(f"a date must be text such as '15/10/2026', not {value!r}")
        parsed = parse_date(value)
        if parsed.value is None:
            raise ValueError(f"not a date ({parsed.reason}): {value!r}")
        return parsed.value, parsed.has_time
    if kind is FieldKind.INTEGER:
        if _is_int(value):
            return value
        if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
            return int(value.strip())
        raise ValueError(f"not a whole number: {value!r}")
    if kind is FieldKind.DECIMAL:
        number: Decimal | None = None
        if _is_int(value) or isinstance(value, Decimal):
            number = Decimal(value)
        elif isinstance(value, str):
            try:
                number = Decimal(value.strip().removesuffix("%").strip())
            except InvalidOperation:
                number = None
        if number is None or not number.is_finite():
            raise ValueError(f"not a number: {value!r}")
        return number
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"text value must be a non-empty string, not {value!r}")
    return normalise_text(value)
