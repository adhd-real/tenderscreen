"""Parse an Indian rupee amount written in figures into integer paise.

``parse_inr("Rs. 25.00 Lacs")`` gives ``AmountResult(paise=250000000, reason=None)``.

It never guesses (CLAUDE.md rule 3). The accepted shape is strict::

    [label:] [currency] number [lakh|crore] [currency] [only|approx|मात्र ...]

for example "EMD: Rs. 25,000/- only", "₹ 2.5 crore (approx)", "25 लाख रुपये".
A currency marker or a lakh/crore unit is required, and an amount with a unit but
no currency marker must stand alone ("2.5 Cr"), because "Quantity: 25 lakh" is
not money. Anything else is rejected with a reason: words before the amount
without a "label:" colon, words after it other than "only"/"approx" (a rate such
as "per day" or a quantity unit such as "MT" changes the meaning), amounts or
scales in words, scale abbreviations ("25 L", "500k", "Rs. in Mn"), negatives,
more than one number, foreign currencies, percentages, unusual digit grouping,
digits touching letters ("Rs. l5,000" from OCR), superscript digits, letters in
scripts other than Latin and Devanagari, and precision finer than one paisa.

Money is handled only as ``decimal.Decimal`` in an exact context and returned as
``int`` paise; it never passes through ``float`` (CLAUDE.md rule 1).
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, InvalidOperation, Overflow
from enum import StrEnum
from typing import Final

from tenderscreen.parsing.text import (
    DEVANAGARI,
    has_compatibility_digits,
    has_foreign_script,
    normalise_text,
)

MAX_TEXT_CHARS: Final = 300
PAISE_PER_RUPEE: Final = 100
LAKH: Final = 100_000
CRORE: Final = 10_000_000

# Exact arithmetic regardless of the caller's decimal context: enough precision
# for any figure that fits in MAX_TEXT_CHARS, and inexact results raise.
_EXACT: Final = Context(prec=MAX_TEXT_CHARS + 30, traps=[Inexact, InvalidOperation, Overflow])


class AmountRejection(StrEnum):
    """Why a text was not turned into an amount. The value is a readable reason."""

    EMPTY = "empty text"
    TOO_LONG = f"text longer than {MAX_TEXT_CHARS} characters"
    NO_AMOUNT = "no amount in figures"
    WORDS = "amount or scale written in words, which cannot be checked against figures"
    NEGATIVE = "negative amount"
    MULTIPLE = "more than one amount in the text"
    NO_CURRENCY = "no currency marker or lakh/crore unit, so the scale is unknown"
    UNMARKED_WITH_LABEL = (
        "lakh/crore without a currency marker must stand alone; it may be a quantity"
    )
    LABEL = "words before the amount; only a 'label:' prefix is accepted"
    FOREIGN_CURRENCY = "currency other than Indian rupees"
    FOREIGN_SCRIPT = "letters in a script other than Latin or Devanagari"
    PERCENTAGE = "a percentage, not an amount"
    MALFORMED_NUMBER = "number has unusual digits, grouping or punctuation, or touches letters"
    UNIT_MISPLACED = "a scale (lakh, crore, Mn, ...) is not directly after the number, or repeats"
    UNKNOWN_UNIT = "unrecognised scale abbreviation after the number"
    TRAILING_TEXT = "unexpected words after the amount, such as a rate or a quantity unit"
    FRACTIONAL_PAISE = "more decimal places than paise allow"


@dataclass(frozen=True, slots=True)
class AmountResult:
    """Either ``paise`` (an int, never negative) or ``reason``; never both."""

    paise: int | None
    reason: AmountRejection | None

    def __post_init__(self) -> None:
        if (self.paise is None) == (self.reason is None):
            raise ValueError("AmountResult needs exactly one of paise and reason")
        if self.paise is not None and self.paise < 0:
            raise ValueError("AmountResult paise cannot be negative")

    @property
    def ok(self) -> bool:
        return self.paise is not None


def _latin_word(pattern: str) -> str:
    """Match ``pattern`` not glued to other Latin letters (digits may touch it)."""
    return rf"(?<![a-z])(?:{pattern})(?![a-z])"


def _devanagari_word(pattern: str) -> str:
    """Match ``pattern`` as a whole Devanagari word."""
    return rf"(?<![{DEVANAGARI}])(?:{pattern})(?![{DEVANAGARI}])"


def _alternatives(words: list[str]) -> str:
    """Regex alternation of normalised words, longest first."""
    normalised = {normalise_text(word) for word in words}
    return "|".join(re.escape(word) for word in sorted(normalised, key=len, reverse=True))


# Found currency markers are replaced by this sign, so later steps see "¤ 500".
# The input itself may not contain it (an unspecified currency).
MARK: Final = "\N{CURRENCY SIGN}"
_NUKTA: Final = chr(0x093C)
# Every dash a minus could be typed as: hyphen-minus, hyphen, non-breaking hyphen,
# figure dash, en dash, em dash, horizontal bar, minus sign, heavy minus, small
# and full-width hyphen-minus, two- and three-em dashes.
_DASHES: Final = re.escape(
    "".join(
        chr(cp)
        for cp in (
            0x2D, 0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0x2796,
            0xFE58, 0xFE63, 0xFF0D, 0x2E3A, 0x2E3B,
        )
    )
)  # fmt: skip

_CURRENCY: Final = re.compile(
    "|".join(
        [
            "\N{INDIAN RUPEE SIGN}",
            _latin_word(r"rs\.?|inr|rupees?"),
            _devanagari_word(r"(?:रु|रू)\.?"),
            _devanagari_word(_alternatives(["रुपये", "रुपए", "रूपये", "रुपया", "रूपए"])),
            r"/\s*-",  # "20,441/-": the Indian convention for a whole-rupee amount
        ]
    )
)
_FOREIGN_CURRENCY: Final = re.compile(
    rf"[$\N{{EURO SIGN}}\N{{POUND SIGN}}\N{{YEN SIGN}}{MARK}]"
    + "|"
    + _latin_word(r"usd|eur|euros?|gbp|dollars?|pounds? sterling")
)
_PERCENTAGE: Final = re.compile(
    "%|" + _latin_word(r"percent|per\s?cent|pct") + "|" + _devanagari_word("प्रतिशत")
)

_ENGLISH_NUMBER_WORDS: Final = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty", "thirty", "forty", "fourty", "fifty", "sixty",
    "seventy", "eighty", "ninety", "half", "hundred", "hundreds", "thousand", "thousands",
    "million", "millions", "billion", "billions", "trillion", "trillions",
]  # fmt: skip
_HINDI_NUMBER_WORDS: Final = [
    "शून्य", "एक", "दो", "तीन", "चार", "पांच", "पाँच", "छह", "छः", "सात", "आठ", "नौ",
    "दस", "ग्यारह", "बारह", "पंद्रह", "पन्द्रह", "बीस", "पच्चीस", "तीस", "चालीस",
    "पचास", "साठ", "सत्तर", "अस्सी", "नब्बे", "सौ", "हजार", "हज़ार", "अरब", "खरब",
    "डेढ़", "ढाई", "सवा", "साढ़े", "पौने",
]  # fmt: skip
_NUMBER_WORDS: Final = re.compile(
    _latin_word(_alternatives(_ENGLISH_NUMBER_WORDS))
    + "|"
    + _devanagari_word(_alternatives(_HINDI_NUMBER_WORDS))
)
_NEGATION_WORDS: Final = re.compile(_latin_word("minus"))

_LAKH_UNIT: Final = _latin_word(r"(?:lakhs?|lacs?)\.?") + "|" + _devanagari_word("लाख")
_CRORE_UNIT: Final = _latin_word(r"(?:crores?|crs?)\.?") + "|" + _devanagari_word(f"करोड{_NUKTA}?")
_UNIT: Final = re.compile(f"(?P<lakh>{_LAKH_UNIT})|(?P<crore>{_CRORE_UNIT})")

# Scale abbreviations we refuse to interpret: "25 L" may mean lakh, "500k" thousand.
_SCALE_ABBREVIATIONS: Final = frozenset([
    "k", "l", "m", "b", "t", "c", "lk", "lkh", "lkhs", "lks", "lacks", "lakhh",
    "mn", "mln", "mio", "bn", "th", "thou", "thsd", "cro", "cror", "core",
])  # fmt: skip
# Label words that make the amount a deduction.
_NEGATING_LABEL_WORDS: Final = frozenset(["less", "deduct", "deduction", "minus"])
# The only words allowed after the amount: they do not change its meaning.
_TRAILING_WORDS: Final = frozenset(
    normalise_text(w) for w in ["only", "approx", "approximately", "मात्र", "लगभग"]
)
_TRAILING_PUNCTUATION: Final = frozenset(MARK + ".,;:()[]*")
_LEAD_PUNCTUATION: Final = frozenset(MARK + "([")

_NUMBER_TOKEN: Final = re.compile(r"[\d.,]*\d[\d.,]*")
_WORD_OR_CHAR: Final = re.compile(rf"[a-z]+|[{DEVANAGARI}]+|\S")
_DASH: Final = re.compile(f"[{_DASHES}]")
_PLAIN: Final = re.compile(r"\d+")
_INDIAN_GROUPING: Final = re.compile(r"\d{1,2}(?:,\d{2})*,\d{3}")  # 12,34,567
_WESTERN_GROUPING: Final = re.compile(r"\d{1,3}(?:,\d{3})+")  # 1,234,567


def _reject(reason: AmountRejection) -> AmountResult:
    return AmountResult(paise=None, reason=reason)


def _is_letter(ch: str) -> bool:
    """Letters and combining marks (Devanagari vowel signs) of any script."""
    return unicodedata.category(ch)[0] in "LM"


def _to_decimal(token: str) -> tuple[Decimal, int] | None:
    """Exact value and number of decimal places of a figure, or None if malformed."""
    whole, dot, fraction = token.partition(".")
    if dot and not (fraction.isascii() and fraction.isdigit()):
        return None
    if not whole.isascii() or not any(
        p.fullmatch(whole) for p in (_PLAIN, _INDIAN_GROUPING, _WESTERN_GROUPING)
    ):
        return None
    digits = whole.replace(",", "")
    if len(digits) > 1 and digits.startswith("0"):
        return None
    return Decimal(f"{digits}.{fraction}" if dot else digits), len(fraction)


def _prefix_problem(prefix: str) -> tuple[AmountRejection | None, bool]:
    """Check the text before the number: (problem or None, whether a label was given)."""
    if prefix and _is_letter(prefix[-1]):
        return AmountRejection.MALFORMED_NUMBER, False  # "l5,000", "I50": OCR letters
    label, colon, lead = prefix.rpartition(":")
    if not colon:
        label, lead = "", prefix
    if any(_is_letter(ch) for ch in lead):
        return AmountRejection.LABEL, bool(label)  # "EMD - Rs. 500": words but no colon
    if _DASH.search(lead):
        return AmountRejection.NEGATIVE, bool(label)  # "₹ -500", "-₹ 500", "(-) Rs. 500"
    if any(not ch.isspace() and ch not in _LEAD_PUNCTUATION for ch in lead):
        return AmountRejection.LABEL, bool(label)
    words = set(_WORD_OR_CHAR.findall(label))
    if words & _NEGATING_LABEL_WORDS:
        return AmountRejection.NEGATIVE, True
    if words & _SCALE_ABBREVIATIONS:
        return AmountRejection.UNIT_MISPLACED, True  # "(Rs. in L): 25"
    return None, bool(label.strip())


def _suffix_problem(suffix: str) -> AmountRejection | None:
    """Check the text after the number (and its unit): only harmless words may follow."""
    if suffix and _is_letter(suffix[0]):
        return AmountRejection.UNKNOWN_UNIT  # letters glued to the number: "500k", "25lks"
    for token in _WORD_OR_CHAR.findall(suffix):
        if token in _SCALE_ABBREVIATIONS:
            return AmountRejection.UNKNOWN_UNIT
        if token not in _TRAILING_WORDS and token not in _TRAILING_PUNCTUATION:
            return AmountRejection.TRAILING_TEXT
    return None


def _to_paise(value: Decimal, decimals: int, multiplier: int) -> int | None:
    """Exact paise, or None if the amount is finer than one paisa."""
    if multiplier == 1 and decimals > 2:
        return None  # "₹ 1.234" is either a fraction of a paisa or European for 1234
    paise = _EXACT.multiply(_EXACT.multiply(value, Decimal(multiplier)), Decimal(PAISE_PER_RUPEE))
    try:
        return int(paise.to_integral_exact(context=_EXACT))
    except Inexact:
        return None


def parse_inr(text: str) -> AmountResult:
    """Parse one rupee amount in figures into integer paise, or say why not."""
    if len(text) > MAX_TEXT_CHARS:
        return _reject(AmountRejection.TOO_LONG)
    if has_compatibility_digits(text):
        return _reject(AmountRejection.MALFORMED_NUMBER)
    norm = normalise_text(text)
    if not norm:
        return _reject(AmountRejection.EMPTY)
    if has_foreign_script(norm):
        return _reject(AmountRejection.FOREIGN_SCRIPT)
    if _FOREIGN_CURRENCY.search(norm):
        return _reject(AmountRejection.FOREIGN_CURRENCY)
    if _PERCENTAGE.search(norm):
        return _reject(AmountRejection.PERCENTAGE)
    if _NUMBER_WORDS.search(norm):
        return _reject(AmountRejection.WORDS)
    if _NEGATION_WORDS.search(norm):
        return _reject(AmountRejection.NEGATIVE)

    marked = _CURRENCY.sub(MARK, norm)
    tokens = list(_NUMBER_TOKEN.finditer(marked))
    if not tokens:
        return _reject(AmountRejection.NO_AMOUNT)
    if len(tokens) > 1:
        return _reject(AmountRejection.MULTIPLE)
    token = tokens[0]
    figure = token.group().rstrip(".,")  # a trailing full stop or comma is punctuation
    figure_end = token.start() + len(figure)
    prefix, rest = marked[: token.start()], marked[figure_end:]

    problem, labelled = _prefix_problem(prefix)
    if problem is not None:
        return _reject(problem)
    if prefix.rstrip().endswith("(") and rest.lstrip().startswith(")"):
        return _reject(AmountRejection.NEGATIVE)  # accounting brackets: "₹ (1,234.50)"
    parsed = _to_decimal(figure)
    if parsed is None:
        return _reject(AmountRejection.MALFORMED_NUMBER)

    units = list(_UNIT.finditer(marked))
    multiplier = 1
    if units:
        unit = units[0]
        if len(units) > 1 or unit.start() < figure_end or marked[figure_end : unit.start()].strip():
            return _reject(AmountRejection.UNIT_MISPLACED)
        multiplier = LAKH if unit.group("lakh") else CRORE
        rest = marked[unit.end() :]
    problem = _suffix_problem(rest)
    if problem is not None:
        return _reject(problem)

    if MARK not in marked:
        if not units:
            return _reject(AmountRejection.NO_CURRENCY)
        if labelled:
            return _reject(AmountRejection.UNMARKED_WITH_LABEL)
    paise = _to_paise(parsed[0], parsed[1], multiplier)
    if paise is None:
        return _reject(AmountRejection.FRACTIONAL_PAISE)
    return AmountResult(paise=paise, reason=None)
