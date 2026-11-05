import pytest

from tenderscreen.datacheck.language import language_of, letter_counts


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
