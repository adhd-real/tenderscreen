"""Fixtures shared by every test package.

OCR tests need Tesseract with English and Hindi. Locally they are skipped, with
the reason, when it is missing. CI sets TENDERSCREEN_REQUIRE_OCR=1, which turns
that skip into a failure, so OCR tests always run there.

OCR tests, tests that write a file of 1 MB or more, and tests that still take
over 1 s with the shortest reliable timeouts are marked ``@pytest.mark.slow``:
the post-edit hook skips them (``-m "not slow"``), while plain ``pytest`` and CI
run them. A test that uses the ``tesseract`` fixture without the marker stops
the run (see tests/markers.py).
"""

import os
from typing import NoReturn

import pytest

from tenderscreen.documents.errors import OcrUnavailableError
from tenderscreen.documents.ocr import Tesseract, require_tesseract
from tests.documents.pdf_factory import find_devanagari_font
from tests.markers import require_slow_marker_on_ocr_tests

REQUIRE_OCR = os.environ.get("TENDERSCREEN_REQUIRE_OCR") == "1"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Refuse to run an OCR test that is not marked slow."""
    require_slow_marker_on_ocr_tests(items)


def _unavailable(reason: str) -> NoReturn:
    if REQUIRE_OCR:
        pytest.fail(f"TENDERSCREEN_REQUIRE_OCR=1, but {reason}")
    pytest.skip(reason)


@pytest.fixture
def tesseract() -> Tesseract:
    """The real Tesseract with eng+hin; skip (locally) or fail (CI) without it.

    Tests using it must be marked ``@pytest.mark.slow``; the name is reserved.
    """
    try:
        return require_tesseract("eng+hin")
    except OcrUnavailableError as exc:
        _unavailable(f"OCR test skipped because Tesseract is not usable here: {exc}")


@pytest.fixture
def devanagari_font() -> str:
    """Path of a Devanagari TrueType font; skip (locally) or fail (CI) without one."""
    font = find_devanagari_font()
    if font is None:
        _unavailable("Hindi test skipped because no Devanagari font is installed (fonts-noto-core)")
    return font
