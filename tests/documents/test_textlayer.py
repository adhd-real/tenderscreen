import json
import multiprocessing
import sys
import time
from pathlib import Path
from typing import cast

import pypdfium2 as pdfium
import pytest

from tenderscreen.documents import textlayer
from tenderscreen.documents.errors import (
    CorruptDocumentError,
    DocumentTimeoutError,
    FileTooLargeError,
    PageTimeoutError,
    TooManyPagesError,
    WorkerStartError,
)
from tenderscreen.documents.models import ExtractionLimits, ExtractionSettings
from tenderscreen.documents.sandbox import (
    ChildDiedError,
    ChildProtocolError,
    ChildStartError,
    ChildTimeoutError,
    Json,
)
from tenderscreen.documents.textlayer import (
    TextLayerPage,
    check_file,
    clean_text,
    has_usable_text_layer,
    image_coverage,
    page_from_json,
    read_text_layers,
    text_layer_child,
)
from tests.documents.pdf_factory import (
    ENGLISH_NOTICE,
    BlankPage,
    ScannedPage,
    TextPage,
    build_form_bomb,
    build_form_wrapped_scan,
    build_nested_form_scan,
    build_offset_mediabox_scan,
    build_pdf,
    build_pdf_bytes,
)

LIMITS = ExtractionLimits()
SETTINGS = ExtractionSettings()
# A synthetic PDF user password (not a secret).
LOCK_PHRASE = "synthetic-test-lock"


def test_clean_text_normalises_line_endings_and_drops_nul() -> None:
    assert clean_text("a\r\nb\rc\x00d\n") == "a\nb\ncd\n"


def test_check_file_accepts_a_pdf(tmp_path: Path) -> None:
    check_file(build_pdf(tmp_path / "ok.pdf", [TextPage()]), LIMITS)


def test_check_file_accepts_header_after_junk(tmp_path: Path) -> None:
    path = tmp_path / "junk.pdf"
    path.write_bytes(b"\x00" * 100 + b"%PDF-1.7\n")
    check_file(path, LIMITS)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "the file is empty"),
        (b"<html>not a pdf</html>", "no %PDF- header"),
        (b"\x00" * 2000 + b"%PDF-1.7", "no %PDF- header"),
    ],
)
def test_check_file_rejects_non_pdfs(tmp_path: Path, content: bytes, message: str) -> None:
    path = tmp_path / "bad.pdf"
    path.write_bytes(content)
    with pytest.raises(CorruptDocumentError, match=message):
        check_file(path, LIMITS)


def test_check_file_rejects_oversized_before_reading(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "big.pdf", [TextPage()])
    with pytest.raises(FileTooLargeError, match=r"big\.pdf is .* MB, over the .* limit"):
        check_file(path, ExtractionLimits(max_file_bytes=100))


def test_unreadable_pdf_is_reported_as_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "garbage.pdf"
    path.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 4)
    with pytest.raises(CorruptDocumentError, match=r"garbage\.pdf is not a readable PDF"):
        read_text_layers(path)


def coverage_of(path: Path, index: int = 0) -> float:
    document = pdfium.PdfDocument(path)
    try:
        return image_coverage(document[index])
    finally:
        document.close()


def test_image_coverage_of_scanned_text_and_blank_pages(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "pages.pdf", [ScannedPage(), TextPage(), BlankPage()])
    assert coverage_of(path, 0) == pytest.approx(1.0, abs=0.01)
    assert coverage_of(path, 1) == 0.0
    assert coverage_of(path, 2) == 0.0


def test_image_coverage_on_a_page_box_away_from_the_origin(tmp_path: Path) -> None:
    """Found in review: a media box starting at (1000, 1000) used to give 0."""
    path = build_offset_mediabox_scan(tmp_path / "offset.pdf")
    assert coverage_of(path) == pytest.approx(1.0, abs=0.01)


def test_image_coverage_of_a_scan_inside_a_form_xobject(tmp_path: Path) -> None:
    """Found in review: form coordinates were taken as page coordinates."""
    path = build_form_wrapped_scan(tmp_path / "form.pdf")
    assert coverage_of(path) == pytest.approx(1.0, abs=0.01)


def test_image_coverage_through_two_nested_forms(tmp_path: Path) -> None:
    """Found in the second review: only one form level was tested, so the order in
    which form matrices are combined was never checked (the wrong order gives 0)."""
    path = build_nested_form_scan(tmp_path / "nested.pdf")
    assert coverage_of(path) == pytest.approx(0.25, abs=0.01)


class FlatPage:
    """A degenerate page with no visible area (seen in malformed PDFs)."""

    def get_bbox(self) -> tuple[float, float, float, float]:
        return (0.0, 0.0, 0.0, 0.0)


def test_image_coverage_of_zero_area_page_is_zero() -> None:
    assert image_coverage(cast(pdfium.PdfPage, FlatPage())) == 0.0


def test_image_coverage_stops_counting_after_the_object_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    monkeypatch.setattr("tenderscreen.documents.textlayer.MAX_COVERAGE_OBJECTS", 0)
    assert coverage_of(path) == 0.0


def test_read_text_layers_returns_every_page(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "doc.pdf", [TextPage(), BlankPage(), ScannedPage()])
    pages = read_text_layers(path)
    assert [p.page_number for p in pages] == [1, 2, 3]
    assert ENGLISH_NOTICE[0] in pages[0].text
    assert "\r" not in pages[0].text
    assert pages[0].meaningful_chars > 100
    assert (pages[1].text, pages[1].meaningful_chars) == ("", 0)
    assert pages[2].text == ""
    assert pages[2].image_coverage > 0.99


def test_password_protected_pdf_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "locked.pdf"
    path.write_bytes(build_pdf_bytes([TextPage()], password=LOCK_PHRASE))
    with pytest.raises(CorruptDocumentError, match="password"):
        read_text_layers(path)


def test_too_many_pages(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "long.pdf", [BlankPage()] * 3)
    with pytest.raises(TooManyPagesError, match=r"long\.pdf has 3 pages"):
        read_text_layers(path, ExtractionLimits(max_pages=2))


@pytest.fixture
def quick_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reader stuck on a bomb never reads the stop message; it is killed after the
    stop grace either way, and the default 0.5 s only makes the test slow."""
    monkeypatch.setattr("tenderscreen.documents.sandbox.STOP_GRACE_S", 0.05)


def test_pdf_bomb_times_out_without_hanging_the_caller(tmp_path: Path, quick_stop: None) -> None:
    """Found in review: 2 KB of nested forms kept the main process busy for 19 s."""
    path = build_form_bomb(tmp_path / "bomb.pdf", depth=5)
    with pytest.raises(PageTimeoutError, match=r"bomb\.pdf page 1: reading the text layer"):
        read_text_layers(path, ExtractionLimits(page_timeout_s=0.05))


@pytest.mark.slow
@pytest.mark.skipif(sys.platform != "linux", reason="memory caps use Linux RLIMIT_AS")
def test_pdf_bomb_hits_the_memory_cap_in_the_child_not_the_caller(tmp_path: Path) -> None:
    """Found in review: a slightly larger bomb aborted the whole calling process."""
    path = build_form_bomb(tmp_path / "bomb.pdf", depth=6)
    limits = ExtractionLimits(max_memory_bytes=256 * 1024 * 1024)
    # PDFium may abort, crash or exit when an allocation fails; any of these is clean.
    with pytest.raises(CorruptDocumentError, match=r"the worker process .*max_memory_bytes"):
        read_text_layers(path, limits)


# Child side, run in this process so coverage sees it.


def run_child(path: Path, max_pages: int = 1000) -> list[Json]:
    parent, child = multiprocessing.Pipe(duplex=True)
    text_layer_child(child, str(path), max_pages)
    messages = []
    while parent.poll():
        messages.append(json.loads(parent.recv_bytes()))
    return messages


def test_child_sends_count_then_pages(tmp_path: Path) -> None:
    messages = [
        cast(list[Json], m)
        for m in run_child(build_pdf(tmp_path / "doc.pdf", [TextPage(), BlankPage()]))
    ]
    assert messages[0] == ["count", 2]
    assert [m[0] for m in messages[1:]] == ["page", "page"]
    assert ENGLISH_NOTICE[0] in page_from_json(messages[1][1], 1).text


def test_child_stops_after_the_count_when_over_the_page_limit(tmp_path: Path) -> None:
    messages = run_child(build_pdf(tmp_path / "doc.pdf", [BlankPage()] * 3), max_pages=2)
    assert messages == [["count", 3]]


def test_child_reports_unreadable_files(tmp_path: Path) -> None:
    path = tmp_path / "garbage.pdf"
    path.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 4)
    assert run_child(path) == [["error", "Failed to load document (PDFium: Data format error)."]]


def test_child_reports_a_broken_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(document: pdfium.PdfDocument, index: int) -> TextLayerPage:
        raise pdfium.PdfiumError("bad page")

    monkeypatch.setattr(textlayer, "read_page", broken)
    messages = run_child(build_pdf(tmp_path / "doc.pdf", [TextPage()]))
    assert messages == [["count", 1], ["error", "page 1: bad page"]]


GOOD_PAGE: dict[str, Json] = {
    "page_number": 1,
    "text": "Tender",
    "meaningful_chars": 6,
    "image_coverage": 0.5,
}


def test_page_from_json_accepts_a_valid_page() -> None:
    assert page_from_json(GOOD_PAGE, 1) == TextLayerPage(1, "Tender", 6, 0.5)
    assert page_from_json({**GOOD_PAGE, "image_coverage": 1}, 1).image_coverage == 1.0


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {**GOOD_PAGE, "extra": 1},
        {**GOOD_PAGE, "page_number": 2},
        {**GOOD_PAGE, "page_number": True},
        {**GOOD_PAGE, "text": None},
        {**GOOD_PAGE, "meaningful_chars": 99},
        {**GOOD_PAGE, "meaningful_chars": "6"},
        {**GOOD_PAGE, "image_coverage": "half"},
        {**GOOD_PAGE, "image_coverage": True},
        {**GOOD_PAGE, "image_coverage": 1.5},
    ],
)
def test_page_from_json_rejects_malformed_pages(bad: Json) -> None:
    with pytest.raises(ValueError, match="page message"):
        page_from_json(bad, 1)


class ScriptedChild:
    """Plays back messages (or raises) instead of running a real child process."""

    def __init__(self, script: list[Json | Exception]) -> None:
        self.script = script
        self.stopped = False

    def receive(self, timeout: float) -> Json:
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def stop(self) -> None:
        self.stopped = True


@pytest.mark.parametrize(
    ("script", "error", "message"),
    [
        ([["error", "boom"]], CorruptDocumentError, "boom"),
        ([["count", -1]], CorruptDocumentError, "unexpected page count"),
        ([["count", "1"]], CorruptDocumentError, "unexpected page count"),
        ([["what", 1]], CorruptDocumentError, "unexpected page count"),
        ([["count", 1], ["error", "page 1: x"]], CorruptDocumentError, "page 1: x"),
        ([["count", 1], ["count", 1]], CorruptDocumentError, "unexpected message"),
        ([["count", 1], "not a pair"], CorruptDocumentError, "unexpected message"),
        ([["count", 1], ["page", {}]], CorruptDocumentError, "wrong keys"),
        ([ChildTimeoutError()], PageTimeoutError, "doc.pdf: opening the PDF took longer"),
        ([["count", 1], ChildTimeoutError()], PageTimeoutError, "doc.pdf page 1: reading"),
        (
            [ChildDiedError(-11)],
            CorruptDocumentError,
            r"SIGSEGV while opening the PDF \(the file\)",
        ),
        (
            [["count", 2], ChildDiedError(-9)],
            CorruptDocumentError,
            r"SIGKILL while reading .*\(page 1\)",
        ),
        ([ChildProtocolError("too big")], CorruptDocumentError, "invalid output: too big"),
    ],
)
def test_read_text_layers_maps_every_child_problem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script: list[Json | Exception],
    error: type[Exception],
    message: str,
) -> None:
    child = ScriptedChild(script)
    monkeypatch.setattr(textlayer, "start_child", lambda target, args, memory: child)
    with pytest.raises(error, match=message):
        read_text_layers(build_pdf(tmp_path / "doc.pdf", [TextPage()]))
    assert child.stopped


def test_child_that_cannot_start_is_a_worker_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cannot_start(target: object, args: object, memory: object) -> None:
        raise ChildStartError("no processes left")

    monkeypatch.setattr(textlayer, "start_child", cannot_start)
    with pytest.raises(WorkerStartError, match="no processes left"):
        read_text_layers(build_pdf(tmp_path / "doc.pdf", [TextPage()]))


@pytest.mark.parametrize(
    ("chars", "coverage", "usable"),
    [
        (0, 0.0, False),
        (39, 0.0, False),  # below min_text_chars
        (40, 0.0, True),
        (150, 1.0, False),  # a scan with a signature overlay: OCR it
        (199, 0.5, False),
        (200, 1.0, True),  # a scan that already has a full OCR text layer
        (150, 0.49, True),  # a text page with a large logo
    ],
)
def test_has_usable_text_layer(chars: int, coverage: float, usable: bool) -> None:
    page = TextLayerPage(1, "x" * chars, chars, coverage)
    assert has_usable_text_layer(page, SETTINGS) is usable


def test_document_deadline_already_passed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    child = ScriptedChild([["count", 1]])
    monkeypatch.setattr(textlayer, "start_child", lambda target, args, memory: child)
    path = build_pdf(tmp_path / "doc.pdf", [TextPage()])
    with pytest.raises(DocumentTimeoutError, match="max_document_s"):
        read_text_layers(path, deadline=time.monotonic() - 1)
    assert child.stopped


def test_document_deadline_shorter_than_the_page_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = ScriptedChild([["count", 3], ChildTimeoutError()])
    monkeypatch.setattr(textlayer, "start_child", lambda target, args, memory: child)
    path = build_pdf(tmp_path / "doc.pdf", [TextPage()])
    with pytest.raises(DocumentTimeoutError, match=r"doc\.pdf: the whole document took longer"):
        read_text_layers(path, deadline=time.monotonic() + 5)  # 5 s < the 60 s page limit


def test_real_document_deadline_on_a_slow_pdf(tmp_path: Path, quick_stop: None) -> None:
    path = build_form_bomb(tmp_path / "bomb.pdf", depth=5)
    # 0.05 s always runs out while the real reader starts. A longer limit would race
    # the bomb, which reads in under a second; the deadline falling mid-document is
    # covered by test_document_deadline_shorter_than_the_page_limit.
    limits = ExtractionLimits(max_document_s=0.05)
    with pytest.raises(DocumentTimeoutError):
        read_text_layers(path, limits)
