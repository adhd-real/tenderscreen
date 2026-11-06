"""Settings and results for page text extraction."""

import math
import os
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# Characters that signal a broken text layer: private-use glyph codes (fonts
# without a Unicode map), unassigned or surrogate code points, the replacement
# character, and control characters other than ordinary whitespace.
_SUSPICIOUS_CATEGORIES: Final = frozenset({"Co", "Cn", "Cs", "Cc"})
_ORDINARY_WHITESPACE: Final = frozenset("\n\t\r\f\v ")
REPLACEMENT_CHARACTER: Final = chr(0xFFFD)


class ExtractionMethod(StrEnum):
    TEXT_LAYER = "text_layer"
    OCR = "ocr"


class PageQuality(StrEnum):
    """How far to trust a page's text.

    good:  usable text (an intact text layer, or OCR with high mean confidence).
    low:   text exists but may be wrong (low OCR confidence, or a text layer with
           many broken characters); check the page by eye before relying on it.
    empty: no letters or digits at all (blank page, or OCR found nothing).
    """

    GOOD = "good"
    LOW = "low"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    """Hard limits; exceeding one raises a clear ``DocumentError``."""

    max_file_bytes: int = 100 * 1024 * 1024
    max_pages: int = 1000
    page_timeout_s: float = 60.0
    # Address-space cap for each worker process that parses or OCRs a document
    # (Linux only). Normal pages need well under 300 MB; a "PDF bomb" hits this.
    max_memory_bytes: int = 1024 * 1024 * 1024
    # The whole document, all pages and stages together. Without it, a crafted
    # 1,000-page file could spend just under 60 s on every page (over 16 hours).
    max_document_s: float = 1800.0

    def __post_init__(self) -> None:
        values = (
            self.max_file_bytes,
            self.max_pages,
            self.page_timeout_s,
            self.max_memory_bytes,
            self.max_document_s,
        )
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError("ExtractionLimits values must all be positive and finite")


@dataclass(frozen=True, slots=True)
class ExtractionSettings:
    """How pages are judged and OCR'd. Changing these can change the output."""

    # Tesseract language codes, joined with "+".
    languages: str = "eng+hin"
    # A page needs at least this many letters/digits to count as having a text layer.
    min_text_chars: int = 40
    # A page mostly covered by images (a scan) needs this many letters/digits to
    # trust its text layer; scans often carry a small overlay such as "Digitally
    # signed by ..." that must not stop the scan itself being OCR'd.
    image_page_min_text_chars: int = 200
    image_coverage_threshold: float = 0.5
    ocr_dpi: int = 300
    # Very large pages are rendered at a lower resolution to bound memory.
    max_render_pixels: int = 50_000_000
    # Mean Tesseract word confidence (0-100) below which OCR text is "low" quality.
    min_ocr_confidence: float = 60.0
    # Share of non-space characters that may be broken before a text layer is "low".
    max_suspicious_share: float = 0.05
    # Worker processes for OCR; None means one per CPU.
    workers: int | None = None
    tesseract_binary: str = "tesseract"

    def __post_init__(self) -> None:
        if not self.languages or self.min_text_chars < 1 or self.ocr_dpi < 72:
            raise ValueError("ExtractionSettings: languages, min_text_chars or ocr_dpi invalid")
        if self.workers is not None and self.workers < 1:
            raise ValueError("ExtractionSettings.workers must be at least 1")

    def worker_count(self, tasks: int) -> int:
        """Processes to start for ``tasks`` OCR pages: never more than needed."""
        wanted = self.workers if self.workers is not None else usable_cpus()
        return max(1, min(wanted, tasks))


def usable_cpus() -> int:
    """CPUs this process may run on (respects CPU affinity, e.g. in containers)."""
    if hasattr(os, "sched_getaffinity"):
        return max(1, len(os.sched_getaffinity(0)))
    return os.cpu_count() or 2


@dataclass(frozen=True, slots=True)
class PageText:
    """The text of one page and how it was obtained."""

    page_number: int  # 1-based, as people and CLAUDE.md's source_page count pages
    text: str
    method: ExtractionMethod
    quality: PageQuality
    # Mean Tesseract word confidence 0-100 for OCR pages; None for text-layer pages.
    ocr_confidence: float | None = None


def meaningful_chars(text: str) -> int:
    """Count letters, digits and combining marks (Devanagari vowel signs)."""
    return sum(1 for ch in text if unicodedata.category(ch)[0] in "LNM")


def suspicious_share(text: str) -> float:
    """Share of non-whitespace characters that look like a broken text layer."""
    visible = [ch for ch in text if ch not in _ORDINARY_WHITESPACE]
    if not visible:
        return 0.0
    bad = sum(
        1
        for ch in visible
        if ch == REPLACEMENT_CHARACTER or unicodedata.category(ch) in _SUSPICIOUS_CATEGORIES
    )
    return bad / len(visible)


def assess_quality(
    text: str, method: ExtractionMethod, confidence: float | None, settings: ExtractionSettings
) -> PageQuality:
    """Quality flag for a page's final text."""
    if meaningful_chars(text) == 0:
        return PageQuality.EMPTY
    if suspicious_share(text) > settings.max_suspicious_share:
        return PageQuality.LOW
    if method is ExtractionMethod.OCR and (
        confidence is None or confidence < settings.min_ocr_confidence
    ):
        return PageQuality.LOW
    return PageQuality.GOOD
