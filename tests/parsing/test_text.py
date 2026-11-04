import pytest

from tenderscreen.parsing.text import (
    ascii_digits,
    has_compatibility_digits,
    has_foreign_script,
    normalise_text,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Rs.\u00a0२५\u200b  LAKH ", "rs. 25 lakh"),
        ("₨ \uff12\uff15", "rs 25"),
        ("੧੨ Oct", "12 oct"),
        ("a\tb\nc", "a b c"),
        ("", ""),
        ("\ufeff \u200d", ""),
    ],
)
def test_normalise_text(raw: str, expected: str) -> None:
    assert normalise_text(raw) == expected


def test_normalise_text_decomposes_precomposed_nukta() -> None:
    # U+095C (precomposed) and U+0921 U+093C (decomposed) must compare equal.
    assert normalise_text("\u095c") == normalise_text("ड\u093c") == "ड\u093c"


def test_ascii_digits_converts_indic_digits_only() -> None:
    assert ascii_digits("२०२६ and ੧੨ and 34") == "2026 and 12 and 34"
    assert ascii_digits("plain ascii 99") == "plain ascii 99"
    assert ascii_digits("½ ²") == "½ ²"  # not decimal digits: left alone


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Rs. 5000", False),
        ("२५ लाख", False),  # Devanagari digits are decimal digits
        ("\uff12\uff15", False),  # full-width digits are decimal digits
        ("5000\u00b2", True),  # superscript two
        ("H\u2082O", True),  # subscript two
        ("\u2464", True),  # circled five
        ("2\u00bd", True),  # one half
        ("\u216b", True),  # Roman numeral twelve
        ("", False),
    ],
)
def test_has_compatibility_digits(text: str, expected: bool) -> None:
    assert has_compatibility_digits(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("rs. 25 lakh", False),
        ("25 लाख रुपये मात्र", False),
        ("caf\u00e9 500", False),  # Latin letter with accent
        ("500 \u20b9 /- 123 ...", False),  # no letters at all
        ("\u09b2\u09be\u0996", True),  # Bengali
        ("\u0a30\u0a41\u0a2a\u0a0f", True),  # Gurmukhi
        ("\u03bc", True),  # Greek
        ("a\u0301", True),  # stray combining accent (not composed)
    ],
)
def test_has_foreign_script(text: str, expected: bool) -> None:
    assert has_foreign_script(text) is expected
