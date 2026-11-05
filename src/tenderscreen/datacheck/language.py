"""Guess a document's language from the share of Devanagari vs Latin letters."""

import unicodedata
from typing import Final

HINDI_SHARE: Final = 0.8  # at least this share of letters Devanagari: "hindi"
ENGLISH_SHARE: Final = 0.2  # at most this share Devanagari: "english"
MIN_LETTERS: Final = 20  # fewer letters than this: too little text to say


def letter_counts(text: str) -> tuple[int, int, int]:
    """(Devanagari, Latin, other-script) letters and vowel signs in ``text``."""
    devanagari = latin = other = 0
    for ch in text:
        if unicodedata.category(ch)[0] not in "LM":
            continue
        name = unicodedata.name(ch, "")
        if name.startswith("DEVANAGARI"):
            devanagari += 1
        elif name.startswith("LATIN"):
            latin += 1
        else:
            other += 1
    return devanagari, latin, other


def language_of(text: str) -> str:
    """ "hindi", "english", "hindi+english", "other script" (e.g. Gurmukhi), or "".

    "" means too little text to say; the cell is then left for a person to fill.
    """
    devanagari, latin, other = letter_counts(text)
    if devanagari + latin + other < MIN_LETTERS:
        return ""
    if other > devanagari + latin:
        return "other script"
    share = devanagari / (devanagari + latin)
    if share >= HINDI_SHARE:
        return "hindi"
    if share <= ENGLISH_SHARE:
        return "english"
    return "hindi+english"
