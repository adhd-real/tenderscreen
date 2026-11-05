"""The kit's OCR path: language of scanned PDFs, through the result cache."""

import runpy
import sys
from pathlib import Path

import pytest

from tenderscreen.datacheck.inspect import inspect_file
from tenderscreen.datacheck.records import template_csv
from tenderscreen.datacheck.run import RECORDS_NAME, run
from tenderscreen.documents.errors import OcrUnavailableError
from tenderscreen.documents.ocr import Tesseract
from tenderscreen.store import open_store
from tests.documents.pdf_factory import ENGLISH_NOTICE, ScannedPage, build_pdf

LONG_SCAN = ScannedPage(ENGLISH_NOTICE * 2)


def test_scanned_pdf_language_comes_from_ocr(tmp_path: Path, tesseract: Tesseract) -> None:
    path = build_pdf(tmp_path / "scan.pdf", [LONG_SCAN])
    with open_store(tmp_path / "raw") as store:
        facts = inspect_file(path, ocr=True, cache=store.cache)
    assert (facts.text_layer_or_scanned, facts.language, facts.problem) == (
        "scanned",
        "english",
        "",
    )


def test_second_run_uses_the_cache_not_ocr(
    tmp_path: Path, tesseract: Tesseract, monkeypatch: pytest.MonkeyPatch
) -> None:
    manual = tmp_path / "manual"
    manual.mkdir()
    (manual / RECORDS_NAME).write_text(template_csv(), encoding="utf-8")
    build_pdf(manual / "scan.pdf", [LONG_SCAN])
    raw = tmp_path / "raw"
    assert "1 new rows" in run(manual, ocr=True, store_root=raw)
    (manual / RECORDS_NAME).write_text(template_csv(), encoding="utf-8")  # start over

    def no_ocr(*args: object, **kwargs: object) -> None:
        raise AssertionError("OCR ran again despite a cached result")

    monkeypatch.setattr("tenderscreen.documents.extract.run_tasks", no_ocr)
    run(manual, ocr=True, store_root=raw)
    assert "scan.pdf,,,,,,,english,pdf,scanned,1," in (manual / RECORDS_NAME).read_text("utf-8")


def test_ocr_problem_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_pdf(tmp_path / "scan.pdf", [LONG_SCAN])

    def unavailable(*args: object, **kwargs: object) -> None:
        raise OcrUnavailableError("the 'tesseract' program was not found or does not run")

    monkeypatch.setattr("tenderscreen.datacheck.inspect.extract_pages", unavailable)
    facts = inspect_file(path, ocr=True)
    assert facts.language == ""
    assert facts.problem.startswith("language not checked: OCR is needed but unavailable")


@pytest.mark.filterwarnings("ignore:.*found in sys.modules:RuntimeWarning")
def test_main_module_in_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manual = tmp_path / "manual"
    manual.mkdir()
    monkeypatch.setattr(sys, "argv", ["datacheck", "--manual-dir", str(manual), "--no-ocr"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("tenderscreen.datacheck", run_name="__main__")
    assert exc.value.code == 0
    assert (manual / RECORDS_NAME).read_text(encoding="utf-8") == template_csv()
