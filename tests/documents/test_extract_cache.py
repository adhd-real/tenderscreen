"""extract_pages through the result cache (Task D acceptance, with Task C's code)."""

import dataclasses
import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tenderscreen.documents import (
    CorruptDocumentError,
    ExtractionLimits,
    ExtractionMethod,
    ExtractionSettings,
    FileTooLargeError,
    PageText,
    TooManyPagesError,
    extract_pages,
)
from tenderscreen.documents import extract as extract_module
from tenderscreen.documents.extract import (
    EXTRACT_PAGES_STEP,
    SETTINGS_NOT_IN_VERSION,
    step_version,
)
from tenderscreen.documents.ocr import Tesseract
from tenderscreen.store import CacheKey, ResultCache, SQLiteBackend
from tests.documents.fake_tesseract import write_fake_tesseract
from tests.documents.pdf_factory import ScannedPage, TextPage, build_pdf
from tests.sqlite_tools import run_sql

NO_OCR = ExtractionSettings(tesseract_binary="tesseract-must-not-be-called")


class CountingExtract:
    """Wraps the real uncached extraction and counts how often it really runs."""

    def __init__(self) -> None:
        self.runs = 0
        self.real = extract_module._extract

    def __call__(
        self, path: Path, limits: ExtractionLimits, settings: ExtractionSettings
    ) -> list[PageText]:
        self.runs += 1
        return self.real(path, limits, settings)


@pytest.fixture
def counter(monkeypatch: pytest.MonkeyPatch) -> CountingExtract:
    counting = CountingExtract()
    monkeypatch.setattr(extract_module, "_extract", counting)
    return counting


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "store.sqlite3"


@pytest.fixture
def cache(db: Path) -> Iterator[ResultCache]:
    backend = SQLiteBackend(db)
    yield ResultCache(backend)
    backend.close()


def test_second_call_is_a_cache_hit(
    tmp_path: Path, cache: ResultCache, counter: CountingExtract
) -> None:
    path = build_pdf(tmp_path / "doc.pdf", [TextPage(), TextPage()])
    first = extract_pages(path, settings=NO_OCR, cache=cache)
    second = extract_pages(path, settings=NO_OCR, cache=cache)
    assert first == second == extract_pages(path, settings=NO_OCR)  # same as uncached
    assert counter.runs == 2  # two cached calls ran once; the uncached call once more
    assert (cache.stats.hits, cache.stats.misses) == (1, 1)


def test_identical_bytes_elsewhere_reuse_the_result(
    tmp_path: Path, cache: ResultCache, counter: CountingExtract
) -> None:
    path = build_pdf(tmp_path / "doc.pdf", [TextPage()])
    copy = tmp_path / "copy-with-another-name.pdf"
    shutil.copyfile(path, copy)
    extract_pages(path, settings=NO_OCR, cache=cache)
    extract_pages(copy, settings=NO_OCR, cache=cache)
    assert counter.runs == 1


def test_output_changing_setting_is_a_new_version(
    tmp_path: Path, cache: ResultCache, counter: CountingExtract, db: Path
) -> None:
    path = build_pdf(tmp_path / "doc.pdf", [TextPage()])
    extract_pages(path, settings=NO_OCR, cache=cache)
    stricter = replace(NO_OCR, min_text_chars=41)
    assert step_version(stricter) != step_version(NO_OCR)
    extract_pages(path, settings=stricter, cache=cache)
    assert counter.runs == 2
    # Both versions stay: a caller using either setting gets its own result.
    versions = {version for (version,) in run_sql(db, "SELECT version FROM step_results")}
    assert versions == {step_version(NO_OCR), step_version(stricter)}


def test_settings_that_cannot_change_output_keep_the_cache(
    tmp_path: Path, cache: ResultCache, counter: CountingExtract
) -> None:
    path = build_pdf(tmp_path / "doc.pdf", [TextPage()])
    extract_pages(path, settings=NO_OCR, cache=cache)
    extract_pages(path, settings=replace(NO_OCR, workers=7), cache=cache)
    extract_pages(path, limits=ExtractionLimits(page_timeout_s=5), settings=NO_OCR, cache=cache)
    assert counter.runs == 1


def test_tampered_cache_entry_is_recomputed_not_trusted(
    tmp_path: Path, cache: ResultCache, counter: CountingExtract, db: Path
) -> None:
    path = build_pdf(tmp_path / "doc.pdf", [TextPage()])
    honest = extract_pages(path, settings=NO_OCR, cache=cache)
    forged = honest[0].text.replace("TENDER", "FORGED").encode()
    run_sql(db, "UPDATE step_results SET payload = ?", (forged,))  # checksum unchanged
    assert extract_pages(path, settings=NO_OCR, cache=cache) == honest
    assert counter.runs == 2
    assert cache.stats.discarded == 1


def test_invalid_cache_entry_with_good_checksum_is_recomputed(
    tmp_path: Path, cache: ResultCache, counter: CountingExtract
) -> None:
    path = build_pdf(tmp_path / "doc.pdf", [TextPage()])
    honest = extract_pages(path, settings=NO_OCR, cache=cache)
    (key,) = cache_keys(tmp_path / "store.sqlite3")
    cache.put(key, b'{"format": 1, "pages": [{"page_number": 9}]}')  # valid checksum
    assert extract_pages(path, settings=NO_OCR, cache=cache) == honest
    assert cache.stats.discarded == 1


def cache_keys(db: Path) -> list[CacheKey]:
    return [CacheKey(*row) for row in run_sql(db, "SELECT sha256, step, version FROM step_results")]


def test_oversized_file_is_refused_before_it_is_hashed(
    tmp_path: Path, cache: ResultCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_pdf(tmp_path / "big.pdf", [TextPage()])

    def must_not_hash(path: Path) -> str:
        raise AssertionError("hashed a file over the size limit")

    monkeypatch.setattr(extract_module, "sha256_file", must_not_hash)
    with pytest.raises(FileTooLargeError):
        extract_pages(path, limits=ExtractionLimits(max_file_bytes=10), cache=cache)


def test_failures_are_not_cached(tmp_path: Path, cache: ResultCache, db: Path) -> None:
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 4)
    for _ in range(2):
        with pytest.raises(CorruptDocumentError):
            extract_pages(path, settings=NO_OCR, cache=cache)
    assert cache.stats.hits == 0
    assert run_sql(db, "SELECT COUNT(*) FROM step_results") == [(0,)]


def test_ocr_results_are_cached(
    tmp_path: Path, cache: ResultCache, tesseract: Tesseract, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    (first,) = extract_pages(path, cache=cache)
    assert first.method is ExtractionMethod.OCR

    def no_workers(*args: object, **kwargs: object) -> None:
        raise AssertionError("OCR ran again despite a cached result")

    monkeypatch.setattr(extract_module, "run_tasks", no_workers)
    assert extract_pages(path, cache=cache) == [first]


def test_step_version_names_what_can_change_the_output(tmp_path: Path) -> None:
    fake = write_fake_tesseract(tmp_path, "exit 1")
    version = step_version(ExtractionSettings(tesseract_binary=str(fake)))
    assert version.startswith("1;pypdfium2=")
    assert "tesseract=tesseract 0.0.0-fake#" in version  # version plus fingerprint
    assert ";pypdfium2=" in version
    assert "languages=eng+hin" in version
    assert "tesseract=none" in step_version(NO_OCR)
    assert EXTRACT_PAGES_STEP == "extract_pages"


# Found in review.


def test_file_changed_during_extraction_is_not_cached(
    tmp_path: Path, cache: ResultCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file swapped mid-read must not have its pages cached under the old hash."""
    path = build_pdf(tmp_path / "doc.pdf", [TextPage()])
    real = extract_module._extract

    def swap_during_read(
        path: Path, limits: ExtractionLimits, settings: ExtractionSettings
    ) -> list[PageText]:
        pages = real(path, limits, settings)
        build_pdf(path, [TextPage(), TextPage()])  # replaced while being processed
        return pages

    monkeypatch.setattr(extract_module, "_extract", swap_during_read)
    extract_pages(path, settings=NO_OCR, cache=cache)
    assert cache.stats.misses == 1
    assert cache_keys(tmp_path / "store.sqlite3") == []


def test_cached_result_still_respects_the_page_limit(tmp_path: Path, cache: ResultCache) -> None:
    path = build_pdf(tmp_path / "doc.pdf", [TextPage()] * 3)
    extract_pages(path, settings=NO_OCR, cache=cache)
    with pytest.raises(TooManyPagesError, match="has 3 pages, over the limit of 2"):
        extract_pages(path, limits=ExtractionLimits(max_pages=2), settings=NO_OCR, cache=cache)


def test_every_output_setting_is_part_of_the_version() -> None:
    """Found in review: removing a setting from the version kept all tests green."""
    base = step_version(NO_OCR)
    changed: dict[str, Any] = {
        "languages": "eng",
        "min_text_chars": 41,
        "image_page_min_text_chars": 201,
        "image_coverage_threshold": 0.6,
        "ocr_dpi": 299,
        "max_render_pixels": 49_000_000,
        "min_ocr_confidence": 61.0,
        "max_suspicious_share": 0.06,
    }
    names = {f.name for f in dataclasses.fields(ExtractionSettings)}
    assert names == set(changed) | SETTINGS_NOT_IN_VERSION  # a new setting must be added here
    for name, value in changed.items():
        assert step_version(replace(NO_OCR, **{name: value})) != base, name
    assert step_version(replace(NO_OCR, workers=9)) == base
