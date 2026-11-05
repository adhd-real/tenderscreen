import json

import pytest

from tenderscreen.documents.models import ExtractionMethod, PageQuality, PageText
from tenderscreen.documents.serialization import FORMAT, decode_pages, encode_pages

PAGES = [
    PageText(1, "NOTICE INVITING TENDER", ExtractionMethod.TEXT_LAYER, PageQuality.GOOD),
    PageText(2, "निविदा सूचना", ExtractionMethod.OCR, PageQuality.GOOD, 91.5),
    PageText(3, "", ExtractionMethod.OCR, PageQuality.EMPTY, None),
    PageText(4, "faint", ExtractionMethod.OCR, PageQuality.LOW, 12),
]


def page_dict(**overrides: object) -> dict[str, object]:
    page: dict[str, object] = {
        "page_number": 1,
        "text": "x",
        "method": "ocr",
        "quality": "good",
        "ocr_confidence": 90.0,
    }
    page.update(overrides)
    return page


def payload(*pages: object, fmt: object = FORMAT) -> bytes:
    return json.dumps({"format": fmt, "pages": list(pages)}).encode()


def test_round_trip() -> None:
    assert decode_pages(encode_pages(PAGES)) == PAGES


def test_round_trip_empty_document() -> None:
    assert decode_pages(encode_pages([])) == []


def test_encoding_keeps_devanagari_readable() -> None:
    assert "निविदा".encode() in encode_pages(PAGES)


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"\xff\xfe not utf-8",
        b"[]",
        b'{"format": 1}',
        b'{"format": 1, "pages": [], "extra": 1}',
        payload(fmt=2),
        b'{"format": 1, "pages": {}}',
        payload("not a dict"),
        payload({**page_dict(), "extra": 1}),
        payload(page_dict(page_number=2)),
        payload(page_dict(page_number=True)),
        payload(page_dict(page_number="1")),
        payload(page_dict(text=None)),
        payload(page_dict(method="magic")),
        payload(page_dict(quality="excellent")),
        payload(page_dict(ocr_confidence=101)),
        payload(page_dict(ocr_confidence=-1)),
        payload(page_dict(ocr_confidence="high")),
        payload(page_dict(ocr_confidence=True)),
        payload(page_dict(ocr_confidence=10**400)),  # found in review: isfinite() overflowed
        payload(page_dict(method="text_layer", ocr_confidence=90.0)),
        b'{"format": 1, "pages": [{"page_number": 1, "text": "x", "method": "ocr", '
        b'"quality": "good", "ocr_confidence": NaN}]}',
    ],
)
def test_malformed_payloads_raise_value_error(bad: bytes) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - any ValueError means "recompute"
        decode_pages(bad)
