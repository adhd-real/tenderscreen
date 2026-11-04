"""Text normalisation shared by the money and date parsers."""

import unicodedata
from typing import Final

# Zero-width space, non-joiner, joiner, word joiner and byte-order mark: invisible
# characters that PDF text layers and copy-paste leave inside words.
_ZERO_WIDTH: Final = dict.fromkeys((0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))

# Devanagari block, for word boundaries in regular expressions (Python's \b
# treats Devanagari vowel signs as non-word characters, so \b splits words).
DEVANAGARI: Final = f"{chr(0x0900)}-{chr(0x097F)}"


def ascii_digits(text: str) -> str:
    """Replace every Unicode decimal digit (Devanagari, Gurmukhi, ...) with ASCII 0-9."""
    if text.isascii():
        return text
    return "".join(
        str(unicodedata.decimal(ch)) if ch.isdecimal() and not ch.isascii() else ch for ch in text
    )


def has_compatibility_digits(text: str) -> bool:
    """True if ``text`` has digit-like characters that are not plain decimal digits.

    Superscripts ("5000\u00b2", a footnote mark), subscripts, circled digits,
    fractions and Roman numerals would silently become ordinary digits under
    NFKC normalisation, changing the number; callers reject them instead.
    """
    return any(unicodedata.numeric(ch, None) is not None and not ch.isdecimal() for ch in text)


def has_foreign_script(text: str) -> bool:
    """True if ``text`` has letters or marks outside the Latin and Devanagari scripts.

    The parsers only understand English and Hindi words, so a word in any other
    script (a Bengali or Gurmukhi "lakh", say) could change the meaning unseen.
    """
    for ch in text:
        if unicodedata.category(ch)[0] in "LM":
            name = unicodedata.name(ch, "")
            if not name.startswith(("LATIN", "DEVANAGARI")):
                return True
    return False


def normalise_text(text: str) -> str:
    """Canonical form for matching.

    NFKC (so "₨" becomes "Rs", full-width digits become ASCII, precomposed
    nukta letters decompose), zero-width characters removed, every decimal digit
    made ASCII, case folded, and runs of whitespace collapsed to one space.
    """
    text = ascii_digits(unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH))
    return " ".join(text.casefold().split())
