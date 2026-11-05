"""Strict JSON encoding of ``extract_pages`` results, for the result cache.

``decode_pages`` validates everything (exact keys, types, enum values, page
numbers 1..n in order) and raises ``ValueError`` otherwise, so a damaged or
outdated cache entry is recomputed instead of being trusted.
"""

import json
import math
from collections.abc import Sequence
from typing import Any, Final

from tenderscreen.documents.models import ExtractionMethod, PageQuality, PageText

FORMAT: Final = 1
_PAGE_KEYS: Final = frozenset({"page_number", "text", "method", "quality", "ocr_confidence"})


def encode_pages(pages: Sequence[PageText]) -> bytes:
    document = {
        "format": FORMAT,
        "pages": [
            {
                "page_number": page.page_number,
                "text": page.text,
                "method": page.method.value,
                "quality": page.quality.value,
                "ocr_confidence": page.ocr_confidence,
            }
            for page in pages
        ],
    }
    return json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _page(raw: Any, expected_number: int) -> PageText:
    if not isinstance(raw, dict) or set(raw) != _PAGE_KEYS:
        raise ValueError(f"page {expected_number}: wrong keys")
    number, text, confidence = raw["page_number"], raw["text"], raw["ocr_confidence"]
    if type(number) is not int or number != expected_number:
        raise ValueError(f"page {expected_number}: page_number {number!r} out of order")
    if not isinstance(text, str):
        raise ValueError(f"page {expected_number}: text is not a string")
    if confidence is not None and (
        type(confidence) not in (int, float) or not math.isfinite(confidence)
        or not 0 <= confidence <= 100
    ):  # fmt: skip
        raise ValueError(f"page {expected_number}: bad ocr_confidence {confidence!r}")
    method = ExtractionMethod(raw["method"])  # ValueError if unknown
    if method is ExtractionMethod.TEXT_LAYER and confidence is not None:
        raise ValueError(f"page {expected_number}: a text-layer page has no OCR confidence")
    return PageText(number, text, method, PageQuality(raw["quality"]), confidence)


def decode_pages(payload: bytes) -> list[PageText]:
    """Pages from ``encode_pages`` output; ``ValueError`` if anything is off."""
    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document, dict) or set(document) != {"format", "pages"}:
        raise ValueError("cached pages: wrong top-level keys")
    if document["format"] != FORMAT or not isinstance(document["pages"], list):
        raise ValueError("cached pages: unknown format")
    return [_page(raw, number) for number, raw in enumerate(document["pages"], start=1)]
