import re
from decimal import Decimal, localcontext

import pytest

from tenderscreen.parsing.money import (
    CRORE,
    LAKH,
    MAX_TEXT_CHARS,
    AmountRejection,
    AmountResult,
    _alternatives,
    _devanagari_word,
    _latin_word,
    _prefix_problem,
    _reject,
    _suffix_problem,
    _to_decimal,
    _to_paise,
    parse_inr,
)

# The eight examples from the task, verbatim.
REQUIRED_CASES = [
    ("Rs. 25.00 Lacs", 250_000_000),
    ("2.5 Cr", 2_500_000_000),
    ("₹ 2,50,00,000", 2_500_000_000),
    ("INR 3.2 crore", 3_200_000_000),
    ("₹ 20441", 2_044_100),
    ("₹ 1,234.50", 123_450),
    ("25 लाख", 250_000_000),
    ("1 करोड़", 1_000_000_000),
]


@pytest.mark.parametrize(("text", "paise"), REQUIRED_CASES)
def test_required_examples(text: str, paise: int) -> None:
    result = parse_inr(text)
    assert result == AmountResult(paise=paise, reason=None)
    assert result.ok


@pytest.mark.parametrize(
    ("text", "paise"),
    [
        # Unit spellings
        ("Rs 12 lakh", 120_000_000),
        ("Rs 12 Lakhs", 120_000_000),
        ("Rs 12 lac", 120_000_000),
        ("Rs 12 lacs", 120_000_000),
        ("Rs 1.5 Lakh.", 15_000_000),
        ("Rs 3 crores", 3_000_000_000),
        ("Rs 3 Crs.", 3_000_000_000),
        ("Rs 3 CR", 3_000_000_000),
        ("12lakh", 120_000_000),
        ("0.75 crore", 750_000_000),
        ("1.23456789 crore", 1_234_567_890),
        # Currency marker spellings and positions
        ("Rs.500", 50_000),
        ("RS. 500", 50_000),
        ("rs 500", 50_000),
        ("INR 500", 50_000),
        ("500 INR", 50_000),
        ("₹500", 50_000),
        ("₨ 500", 50_000),
        ("Rupees 500", 50_000),
        ("रु. 500", 50_000),
        ("500 रुपये", 50_000),
        # The Indian "/-" convention marks a rupee amount
        ("Rs. 20,441/-", 2_044_100),
        ("20,441/-", 2_044_100),
        ("Rs. 5,000/- only", 500_000),
        # Grouping styles and decimals
        ("₹ 12,34,567.50", 123_456_750),
        ("₹ 1,234,567", 123_456_700),
        ("₹ 0.50", 50),
        ("₹ 0", 0),
        ("₹ 1,234.5", 123_450),
        # Labels and whitespace around a single amount
        ("EMD: Rs. 25,000/-", 2_500_000),
        ("EMD (Rs.): 25,000", 2_500_000),
        ("धरोहर राशि: ₹ 25,000", 2_500_000),
        ("(₹ 500)", 50_000),
        ("₹ 500 (approx.)", 50_000),
        ("  ₹\u00a01,000  ", 100_000),
        ("Estimated cost: Rs. 2.5 Cr (approx)", 2_500_000_000),
        # Non-ASCII digits are real digits
        ("२५ लाख", 250_000_000),
        ("₹ \uff12\uff15", 2_500),
        # Precomposed nukta form of करोड़ (U+095C) and the common no-nukta spelling
        ("1 करो\u095c", 1_000_000_000),
        ("1 करोड", 1_000_000_000),
        ("25 लाख रुपये मात्र", 250_000_000),
    ],
)
def test_accepted_variants(text: str, paise: int) -> None:
    assert parse_inr(text) == AmountResult(paise=paise, reason=None)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", AmountRejection.EMPTY),
        ("   \n\t ", AmountRejection.EMPTY),
        ("₹ " + "1" * MAX_TEXT_CHARS, AmountRejection.TOO_LONG),
        # Negative amounts
        ("-₹ 500", AmountRejection.NEGATIVE),
        ("₹ -500", AmountRejection.NEGATIVE),
        ("Rs. \u22122 lakh", AmountRejection.NEGATIVE),
        ("INR -3.2 crore", AmountRejection.NEGATIVE),
        # More than one amount
        ("Rs. 500 and Rs. 600", AmountRejection.MULTIPLE),
        ("₹ 2,500-3,000", AmountRejection.MULTIPLE),
        ("Rs. 2 crore 50 lakh", AmountRejection.MULTIPLE),
        ("Tender 2026/17 EMD Rs. 500", AmountRejection.MULTIPLE),
        # Amounts written in words (alone, or alongside figures we cannot cross-check)
        ("Rupees Five Lakh only", AmountRejection.WORDS),
        ("Rs. 5,00,000/- (Rupees Five Lakh only)", AmountRejection.WORDS),
        ("twenty five thousand rupees", AmountRejection.WORDS),
        ("Rs. 25 thousand", AmountRejection.WORDS),
        ("पाँच लाख रुपये", AmountRejection.WORDS),
        ("दो करोड़", AmountRejection.WORDS),
        # Nothing to parse
        ("Nil", AmountRejection.NO_AMOUNT),
        ("Rs. ", AmountRejection.NO_AMOUNT),
        # Ambiguous: scale unknown
        ("20441", AmountRejection.NO_CURRENCY),
        ("25.00", AmountRejection.NO_CURRENCY),
        # Ambiguous: unit far from the number ("Rs. in lakhs" table headers)
        ("Rs. in lakhs: 25.00", AmountRejection.UNIT_MISPLACED),
        ("Rs. 25 lakh crore", AmountRejection.UNIT_MISPLACED),
        # Ambiguous: unrecognised scale abbreviations
        ("Rs. 25 L", AmountRejection.UNKNOWN_UNIT),
        ("Rs. 500k", AmountRejection.UNKNOWN_UNIT),
        ("Rs. 2.5 Mn", AmountRejection.UNKNOWN_UNIT),
        ("₹ 25lks", AmountRejection.UNKNOWN_UNIT),
        # Ambiguous: not rupees
        ("$ 500", AmountRejection.FOREIGN_CURRENCY),
        ("USD 2.5 crore", AmountRejection.FOREIGN_CURRENCY),
        ("€500", AmountRejection.FOREIGN_CURRENCY),
        # Ambiguous: a percentage
        ("5%", AmountRejection.PERCENTAGE),
        ("PBG 3 percent", AmountRejection.PERCENTAGE),
        ("10 प्रतिशत", AmountRejection.PERCENTAGE),
        # Ambiguous: odd grouping or punctuation (European style, typos)
        ("₹ 25,00", AmountRejection.MALFORMED_NUMBER),
        ("₹ 1.234.567", AmountRejection.MALFORMED_NUMBER),
        ("₹ 1,23,4567", AmountRejection.MALFORMED_NUMBER),
        ("₹ .5", AmountRejection.MALFORMED_NUMBER),
        ("₹ 0500", AmountRejection.MALFORMED_NUMBER),
        # Ambiguous: precision beyond paise (₹ 1.234 may be European 1234)
        ("₹ 1.234", AmountRejection.FRACTIONAL_PAISE),
        ("₹ 1.235", AmountRejection.FRACTIONAL_PAISE),
        ("1.0000000001 crore", AmountRejection.FRACTIONAL_PAISE),
        ("1.000000000000000000000000000001 crore", AmountRejection.FRACTIONAL_PAISE),
        # Found in review: words after the amount change its meaning
        ("Rs. 25 thousands", AmountRejection.WORDS),
        ("Rs. 25 lacks", AmountRejection.UNKNOWN_UNIT),
        ("Rs. 25 Lakhonly", AmountRejection.TRAILING_TEXT),
        ("Rs. 25 mln", AmountRejection.UNKNOWN_UNIT),
        ("Rs. 2.5 core", AmountRejection.UNKNOWN_UNIT),
        ("₹ 25 कोटी", AmountRejection.TRAILING_TEXT),  # Marathi crore: not understood
        ("₹ 25 लाख\u093c", AmountRejection.TRAILING_TEXT),  # misspelt lakh
        ("Rs. 500 per day", AmountRejection.TRAILING_TEXT),
        ("Rs. 500/day", AmountRejection.TRAILING_TEXT),
        ("₹ 500 प्रति दिन", AmountRejection.TRAILING_TEXT),
        ("25 lakh MT", AmountRejection.TRAILING_TEXT),
        ("2.5 crore units", AmountRejection.TRAILING_TEXT),
        ("Rs 5 lakh + GST", AmountRejection.TRAILING_TEXT),
        ("Rs. 2,00,000/- in the form of BG", AmountRejection.TRAILING_TEXT),
        # Found in review: scales stated before the amount (table headers)
        ("(Rs. in Mn) 25", AmountRejection.LABEL),
        ("(Rs. in Mn): 25", AmountRejection.UNIT_MISPLACED),
        ("(Rs. in L): 25", AmountRejection.UNIT_MISPLACED),
        ("(\u20b9 in Millions) 25.5", AmountRejection.WORDS),
        ("(Rs. in Thousands) 25", AmountRejection.WORDS),
        # Words before the amount must be a "label:"
        ("EMD - Rs. 25,000", AmountRejection.LABEL),
        ("The EMD is Rs. 500", AmountRejection.LABEL),
        ("Quantity: 25 lakh", AmountRejection.UNMARKED_WITH_LABEL),
        # Found in review: OCR letters touching the digits
        ("\u20b9 5,OOO", AmountRejection.TRAILING_TEXT),
        ("Rs. 50,OOO/-", AmountRejection.TRAILING_TEXT),
        ("Rs. l5,000", AmountRejection.MALFORMED_NUMBER),
        ("Rs. I50", AmountRejection.MALFORMED_NUMBER),
        # Found in review: digit-like characters that NFKC would turn into digits
        ("\u20b9 5000\u00b2", AmountRejection.MALFORMED_NUMBER),
        ("\u20b9 2.5\u00b2 crore", AmountRejection.MALFORMED_NUMBER),
        ("\u20b9 \u2464", AmountRejection.MALFORMED_NUMBER),
        ("Rs. 2\u00bd lakh", AmountRejection.MALFORMED_NUMBER),
        # Found in review: negatives written other ways
        ("\u20b9 \u2010500", AmountRejection.NEGATIVE),
        ("\u20b9 \u2012500", AmountRejection.NEGATIVE),
        ("\u20b9 \u2014500", AmountRejection.NEGATIVE),
        ("(-) Rs. 500", AmountRejection.NEGATIVE),
        ("Minus Rs. 500", AmountRejection.NEGATIVE),
        ("Less: Rs. 500", AmountRejection.NEGATIVE),
        ("-Rupees 500", AmountRejection.NEGATIVE),
        ("\u2212रु. 500", AmountRejection.NEGATIVE),
        ("- \u20b9 500", AmountRejection.NEGATIVE),
        ("\u20b9 (1,234.50)", AmountRejection.NEGATIVE),
        # Found in review: scripts we cannot read
        ("\u20b9 \u09e8\u09eb \u09b2\u09be\u0996", AmountRejection.FOREIGN_SCRIPT),
        ("\u0a30\u0a41\u0a2a\u0a0f 500", AmountRejection.FOREIGN_SCRIPT),
        ("\u00a4 500", AmountRejection.FOREIGN_CURRENCY),
    ],
)
def test_rejections_carry_a_reason(text: str, reason: AmountRejection) -> None:
    result = parse_inr(text)
    assert result == AmountResult(paise=None, reason=reason)
    assert not result.ok


def test_sentence_full_stop_after_amount_is_not_a_decimal_point() -> None:
    assert parse_inr("EMD: Rs. 500.").paise == 50_000


def test_result_paise_is_int_never_float() -> None:
    paise = parse_inr("Rs. 1.5 lakh").paise
    assert type(paise) is int


def test_text_at_the_length_limit_is_still_parsed() -> None:
    text = "₹ 500" + " " * (MAX_TEXT_CHARS - 5)
    assert len(text) == MAX_TEXT_CHARS
    assert parse_inr(text).paise == 50_000


def test_result_requires_exactly_one_of_paise_and_reason() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        AmountResult(paise=None, reason=None)
    with pytest.raises(ValueError, match="exactly one"):
        AmountResult(paise=5, reason=AmountRejection.EMPTY)


def test_result_rejects_negative_paise() -> None:
    with pytest.raises(ValueError, match="negative"):
        AmountResult(paise=-1, reason=None)


def test_rejection_text_is_human_readable() -> None:
    assert str(AmountRejection.MULTIPLE) == "more than one amount in the text"


def test_result_does_not_depend_on_the_global_decimal_context() -> None:
    with localcontext() as ctx:
        ctx.prec = 6
        assert parse_inr("\u20b9 12,34,567.50").paise == 123_456_750
        assert parse_inr("1.23456789 crore").paise == 1_234_567_890


# Private helpers (CLAUDE.md rule 6: every function gets its own tests).


def test_latin_word_does_not_match_inside_words() -> None:
    pattern = re.compile(_latin_word("ten"))
    assert pattern.search("ten")
    assert pattern.search("10ten5")
    assert not pattern.search("tender")
    assert not pattern.search("often")


def test_devanagari_word_matches_whole_words_only() -> None:
    pattern = re.compile(_devanagari_word("दो"))
    assert pattern.search("दो करोड़")
    assert not pattern.search("दोनों")


def test_alternatives_normalises_and_orders_longest_first() -> None:
    assert _alternatives(["Rs", "rupees", "RS"]) == "rupees|rs"
    assert _alternatives([]) == ""


def test_reject_builds_a_rejection() -> None:
    assert _reject(AmountRejection.EMPTY) == AmountResult(None, AmountRejection.EMPTY)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("20441", (Decimal("20441"), 0)),
        ("2,50,00,000", (Decimal("25000000"), 0)),
        ("1,234,567.5", (Decimal("1234567.5"), 1)),
        ("0.50", (Decimal("0.50"), 2)),
        ("0", (Decimal("0"), 0)),
        ("25,00", None),
        ("1.2.3", None),
        (".5", None),
        ("05", None),
        ("1,23,4567", None),
        ("1.2,3", None),
    ],
)
def test_to_decimal(token: str, expected: tuple[Decimal, int] | None) -> None:
    assert _to_decimal(token) == expected


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("", (None, False)),
        ("\u00a4 ", (None, False)),
        ("( \u00a4", (None, False)),
        ("emd: \u00a4 ", (None, True)),
        ("emd (\u00a4): ", (None, True)),
        ("\u00a4 l", (AmountRejection.MALFORMED_NUMBER, False)),
        ("\u00a4 -", (AmountRejection.NEGATIVE, False)),
        ("emd - \u00a4 ", (AmountRejection.LABEL, False)),
        ("# \u00a4 ", (AmountRejection.LABEL, False)),
        ("emd \u00a4 ", (AmountRejection.LABEL, False)),
        ("less: \u00a4 ", (AmountRejection.NEGATIVE, True)),
        ("(\u00a4 in mn): ", (AmountRejection.UNIT_MISPLACED, True)),
    ],
)
def test_prefix_problem(prefix: str, expected: tuple[AmountRejection | None, bool]) -> None:
    assert _prefix_problem(prefix) == expected


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        ("", None),
        (" \u00a4 only.", None),
        (" (approx)", None),
        (" मात्र", None),
        ("k", AmountRejection.UNKNOWN_UNIT),
        (" mn", AmountRejection.UNKNOWN_UNIT),
        (" per day", AmountRejection.TRAILING_TEXT),
        ("/day", AmountRejection.TRAILING_TEXT),
        (" + gst", AmountRejection.TRAILING_TEXT),
    ],
)
def test_suffix_problem(suffix: str, expected: AmountRejection | None) -> None:
    assert _suffix_problem(suffix) == expected


@pytest.mark.parametrize(
    ("value", "decimals", "multiplier", "expected"),
    [
        (Decimal("20441"), 0, 1, 2_044_100),
        (Decimal("1234.50"), 2, 1, 123_450),
        (Decimal("1.234"), 3, 1, None),
        (Decimal("2.5"), 1, CRORE, 2_500_000_000),
        (Decimal("0.0000001"), 7, LAKH, 1),
        (Decimal("0.00000001"), 8, LAKH, None),
    ],
)
def test_to_paise(value: Decimal, decimals: int, multiplier: int, expected: int | None) -> None:
    assert _to_paise(value, decimals, multiplier) == expected
