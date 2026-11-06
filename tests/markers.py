"""The rule for the ``slow`` marker, kept out of conftest.py so tests can import it.

The name of the ``tesseract`` fixture (tests/conftest.py) is reserved for the
real Tesseract: a fake must use another name, or this guard asks for it to be
marked slow too.
"""

from collections.abc import Iterable

import pytest


def unmarked_ocr_tests(items: Iterable[pytest.Item]) -> list[str]:
    """Node ids of tests that use the real Tesseract but are not marked slow."""
    return [
        item.nodeid
        for item in items
        if "tesseract" in getattr(item, "fixturenames", ())
        and item.get_closest_marker("slow") is None
    ]


def require_slow_marker_on_ocr_tests(items: Iterable[pytest.Item]) -> None:
    """Stop the run (``pytest.UsageError``) if a test using Tesseract is not marked slow."""
    unmarked = unmarked_ocr_tests(items)
    if unmarked:
        raise pytest.UsageError(
            "tests that use the tesseract fixture (the real Tesseract; give a fake "
            "another name) must be marked @pytest.mark.slow: " + ", ".join(unmarked)
        )
