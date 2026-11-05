import pytest

from tenderscreen.datacheck.language import (
    ENGLISH_SHARE,
    HINDI_SHARE,
    MIN_LETTERS,
    language_from_counts,
    language_of,
    letter_counts,
)


def test_letter_counts() -> None:
    assert letter_counts("Tender सूचना 2026!") == (5, 6, 0)
    assert letter_counts("") == (0, 0, 0)
    assert letter_counts("Привет") == (0, 0, 6)  # Cyrillic: other script


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("NOTICE INVITING TENDER for supply of road material", "english"),
        ("निविदा सूचना: सड़क निर्माण कार्य हेतु निविदा आमंत्रित", "hindi"),
        ("निविदा सूचना Notice Inviting Tender निर्माण कार्य works", "hindi+english"),
        ("Page 1 of 2", ""),  # too little text to say
        ("ਟੈਂਡਰ ਨੋਟਿਸ ਪੰਜਾਬ ਸਰਕਾਰ ਲੋਕ ਨਿਰਮਾਣ ਵਿਭਾਗ", "other script"),  # Gurmukhi
        ("", ""),
    ],
)
def test_language_of(text: str, expected: str) -> None:
    assert language_of(text) == expected


def test_mostly_english_with_a_hindi_heading_is_english() -> None:
    text = "भारत सरकार " + "Government of India tender notice for road works. " * 5
    assert language_of(text) == "english"


# Found in review: the thresholds themselves.


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ((16, 4, 0), "hindi"),  # exactly 80% Devanagari
        ((15, 5, 0), "hindi+english"),  # 75%
        ((4, 16, 0), "english"),  # exactly 20%
        ((5, 15, 0), "hindi+english"),  # 25%
        ((0, 19, 0), ""),  # one letter short of saying anything
        ((0, 20, 0), "english"),
        ((0, 0, 20), "other script"),
        ((5, 5, 10), "hindi+english"),  # other script not the majority
        ((5, 5, 11), "other script"),
        ((0, 0, 0), ""),
    ],
)
def test_language_from_counts_thresholds(counts: tuple[int, int, int], expected: str) -> None:
    assert language_from_counts(*counts) == expected


def test_threshold_constants() -> None:
    assert (HINDI_SHARE, ENGLISH_SHARE, MIN_LETTERS) == (0.8, 0.2, 20)


def test_language_of_counts_letters_not_digits_or_punctuation() -> None:
    assert language_of("1234567890 !@#$%^&*() " * 5) == ""
    assert language_of("a" * 19 + " 2026") == ""
    assert language_of("a" * 20) == "english"
