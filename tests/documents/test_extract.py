"""Acceptance tests for extract_pages (Task C)."""

import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from tenderscreen.documents import (
    CorruptDocumentError,
    DocumentError,
    ExtractionLimits,
    ExtractionMethod,
    ExtractionSettings,
    FileTooLargeError,
    OcrFailedError,
    OcrUnavailableError,
    PageQuality,
    PageTimeoutError,
    TooManyPagesError,
    WorkerStartError,
    extract_pages,
)
from tenderscreen.documents.ocr import Tesseract
from tenderscreen.documents.sandbox import ChildStartError
from tests.documents.fake_tesseract import write_fake_tesseract
from tests.documents.pdf_factory import (
    AWARD_SCAN,
    ENGLISH_NOTICE,
    HINDI_NOTICE,
    SIGNATURE,
    BlankPage,
    ScannedPage,
    TextPage,
    build_form_bomb,
    build_form_wrapped_scan,
    build_offset_mediabox_scan,
    build_pdf,
    build_pdf_bytes,
)

# Any OCR attempt with this setting fails loudly, proving text pages need no OCR.
NO_OCR = ExtractionSettings(tesseract_binary="tesseract-must-not-be-called")
# A synthetic PDF user password (not a secret).
LOCK_PHRASE = "synthetic-test-lock"


def test_text_layer_pdf_needs_no_ocr(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "text.pdf", [TextPage(), TextPage(ENGLISH_NOTICE[::-1])])
    pages = extract_pages(path, settings=NO_OCR)
    assert [(p.page_number, p.method, p.quality) for p in pages] == [
        (1, ExtractionMethod.TEXT_LAYER, PageQuality.GOOD),
        (2, ExtractionMethod.TEXT_LAYER, PageQuality.GOOD),
    ]
    assert all(line in pages[0].text for line in ENGLISH_NOTICE)
    assert pages[0].ocr_confidence is None


def test_accepts_str_path(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "text.pdf", [TextPage()])
    assert extract_pages(str(path), settings=NO_OCR)[0].page_number == 1


def test_scanned_page_is_ocred(tmp_path: Path, tesseract: Tesseract) -> None:
    path = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    (page,) = extract_pages(path)
    assert (page.page_number, page.method, page.quality) == (
        1,
        ExtractionMethod.OCR,
        PageQuality.GOOD,
    )
    assert all(line in page.text for line in AWARD_SCAN)
    assert page.ocr_confidence is not None


def test_mixed_document_gets_the_right_method_on_each_page(
    tmp_path: Path, tesseract: Tesseract
) -> None:
    """Acceptance: text pages keep their text layer; scans, even with a signature
    overlay in the text layer, and blank pages go to OCR."""
    path = build_pdf(
        tmp_path / "mixed.pdf",
        [TextPage(), ScannedPage(overlay=SIGNATURE), BlankPage(), ScannedPage(), TextPage()],
    )
    pages = extract_pages(path, settings=ExtractionSettings(workers=3))
    assert [(p.page_number, p.method, p.quality) for p in pages] == [
        (1, ExtractionMethod.TEXT_LAYER, PageQuality.GOOD),
        (2, ExtractionMethod.OCR, PageQuality.GOOD),
        (3, ExtractionMethod.OCR, PageQuality.EMPTY),
        (4, ExtractionMethod.OCR, PageQuality.GOOD),
        (5, ExtractionMethod.TEXT_LAYER, PageQuality.GOOD),
    ]
    assert "AWARD OF CONTRACT" in pages[1].text
    assert pages[2].text == ""
    assert ENGLISH_NOTICE[0] in pages[4].text


def test_hindi_text_layer(tmp_path: Path, devanagari_font: str) -> None:
    path = build_pdf(tmp_path / "hindi.pdf", [TextPage(HINDI_NOTICE, font_path=devanagari_font)])
    (page,) = extract_pages(path, settings=NO_OCR)
    assert page.method is ExtractionMethod.TEXT_LAYER
    assert all(line in page.text for line in HINDI_NOTICE)


def test_hindi_scan_is_ocred(tmp_path: Path, tesseract: Tesseract, devanagari_font: str) -> None:
    path = build_pdf(tmp_path / "hindi.pdf", [ScannedPage(HINDI_NOTICE, font_path=devanagari_font)])
    (page,) = extract_pages(path)
    assert page.method is ExtractionMethod.OCR
    assert all(line in page.text for line in HINDI_NOTICE)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "the file is empty"),
        (b"This is a text file, not a PDF.", "no %PDF- header"),
        (b"%PDF-1.7\n" + bytes(range(256)) * 8, "Data format error"),
        (build_pdf_bytes([TextPage()])[:600], "Data format error"),  # truncated download
    ],
)
def test_corrupt_files_fail_cleanly(tmp_path: Path, content: bytes, message: str) -> None:
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(content)
    with pytest.raises(CorruptDocumentError, match=message) as caught:
        extract_pages(path, settings=NO_OCR)
    assert isinstance(caught.value, DocumentError)
    assert str(caught.value).startswith("corrupt.pdf is not a readable PDF")


def test_password_protected_file_fails_cleanly(tmp_path: Path) -> None:
    path = tmp_path / "locked.pdf"
    path.write_bytes(build_pdf_bytes([TextPage()], password=LOCK_PHRASE))
    with pytest.raises(CorruptDocumentError, match="password"):
        extract_pages(path, settings=NO_OCR)


def test_oversized_file_fails_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_pdf(tmp_path / "big.pdf", [TextPage()])

    def must_not_start(*args: object, **kwargs: object) -> None:
        raise AssertionError("an oversized file must not reach the PDF parser")

    monkeypatch.setattr("tenderscreen.documents.textlayer.start_child", must_not_start)
    limits = ExtractionLimits(max_file_bytes=path.stat().st_size - 1)
    with pytest.raises(FileTooLargeError, match=r"big\.pdf is 0\.0 MB, over the"):
        extract_pages(path, limits=limits, settings=NO_OCR)


def test_too_many_pages_fails_cleanly(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "long.pdf", [TextPage()] * 3)
    with pytest.raises(TooManyPagesError, match=r"long\.pdf has 3 pages, over the limit of 2"):
        extract_pages(path, limits=ExtractionLimits(max_pages=2), settings=NO_OCR)


def test_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        extract_pages(Path("/nonexistent/notice.pdf"))


def test_ocr_unavailable_is_a_clear_error(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    with pytest.raises(OcrUnavailableError, match="apt-get install -y tesseract-ocr"):
        extract_pages(path, settings=NO_OCR)


def test_slow_ocr_page_times_out(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "slow.pdf", [TextPage(), ScannedPage()])
    fake = write_fake_tesseract(tmp_path, "exec sleep 30")
    settings = ExtractionSettings(tesseract_binary=str(fake))
    with pytest.raises(PageTimeoutError, match=r"slow\.pdf page 2: rendering and OCR took longer"):
        extract_pages(path, limits=ExtractionLimits(page_timeout_s=1.0), settings=settings)


def ocr_work_dirs(root: Path) -> list[Path]:
    return sorted(root.glob("tenderscreen-ocr-*"))


def test_hung_worker_is_killed_with_its_tesseract_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a worker never answers (e.g. rendering hangs), the parent's deadline still
    fires, the worker and (on Linux) its Tesseract die, and no temporary files remain."""
    path = build_pdf(tmp_path / "hang.pdf", [ScannedPage()])
    pid_file = tmp_path / "tesseract.pid"
    fake = write_fake_tesseract(tmp_path, f'echo $$ > "{pid_file}"; exec sleep 60')
    # Worker's own limit 20 s; the parent's deadline 20 - 17 = 3 s after the page starts.
    monkeypatch.setattr("tenderscreen.documents.extract.WORKER_GRACE_S", -17.0)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    settings = ExtractionSettings(tesseract_binary=str(fake))
    with pytest.raises(PageTimeoutError, match=r"hang\.pdf page 1"):
        extract_pages(path, limits=ExtractionLimits(page_timeout_s=20.0), settings=settings)
    assert ocr_work_dirs(tmp_path) == []
    if sys.platform == "linux":
        pid = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 10
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not process_alive(pid), "Tesseract outlived its killed worker"


def process_alive(pid: int) -> bool:
    """True if ``pid`` is a running (not zombie) process; Linux /proc only."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def test_successful_ocr_leaves_no_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    fake = write_fake_tesseract(tmp_path, 'printf "AWARD\\n" > "$2.txt"; : > "$2.tsv"')
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    (page,) = extract_pages(path, settings=ExtractionSettings(tesseract_binary=str(fake)))
    assert page.text == "AWARD"
    assert ocr_work_dirs(tmp_path) == []


def test_failing_ocr_is_a_clear_error(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    fake = write_fake_tesseract(tmp_path, "echo 'cannot read image' >&2; exit 1")
    settings = ExtractionSettings(tesseract_binary=str(fake))
    expected = r"scan\.pdf page 1: OCR failed: tesseract: cannot read"
    with pytest.raises(OcrFailedError, match=expected):
        extract_pages(path, settings=settings)


def test_crashed_worker_is_a_clear_error_at_once(tmp_path: Path) -> None:
    """A worker killed mid-page (here by its own Tesseract) is reported as such, at once."""
    path = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    fake = write_fake_tesseract(tmp_path, "kill -9 $PPID; exec sleep 60")
    settings = ExtractionSettings(tesseract_binary=str(fake))
    started = time.monotonic()
    with pytest.raises(OcrFailedError, match=r"page 1: OCR failed: .*killed by SIGKILL"):
        extract_pages(path, settings=settings)
    assert time.monotonic() - started < 30  # not the 60 s page limit


def test_first_failing_page_stops_the_document(tmp_path: Path) -> None:
    """Found in review: one failure used to wait for every other page first."""
    path = build_pdf(tmp_path / "many.pdf", [ScannedPage()] * 6)
    fake = write_fake_tesseract(
        tmp_path, 'case "$1" in */page-1/*) echo bad >&2; exit 1;; *) exec sleep 60;; esac'
    )
    settings = ExtractionSettings(tesseract_binary=str(fake), workers=2)
    started = time.monotonic()
    with pytest.raises(OcrFailedError, match=r"many\.pdf page 1: OCR failed: tesseract: bad"):
        extract_pages(path, limits=ExtractionLimits(page_timeout_s=30.0), settings=settings)
    assert time.monotonic() - started < 20  # far less than one 30 s page timeout


def test_worker_that_cannot_start_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    fake = write_fake_tesseract(tmp_path, "exit 1")

    def cannot_start(*args: object, **kwargs: object) -> None:
        raise ChildStartError("the worker process did not start within 60 s")

    monkeypatch.setattr("tenderscreen.documents.extract.run_tasks", cannot_start)
    with pytest.raises(WorkerStartError, match="did not start within 60 s"):
        extract_pages(path, settings=ExtractionSettings(tesseract_binary=str(fake)))


@pytest.mark.parametrize("build", [build_offset_mediabox_scan, build_form_wrapped_scan])
def test_scans_in_unusual_page_geometry_are_ocred(
    tmp_path: Path, tesseract: Tesseract, build: Callable[[Path], Path]
) -> None:
    """Found in review: these scans were taken as text pages, losing the scanned body."""
    (page,) = extract_pages(build(tmp_path / "scan.pdf"))
    assert page.method is ExtractionMethod.OCR
    assert "AWARD OF CONTRACT" in page.text


def test_pdf_bomb_fails_cleanly(tmp_path: Path) -> None:
    path = build_form_bomb(tmp_path / "bomb.pdf", depth=5)
    with pytest.raises(PageTimeoutError):
        extract_pages(path, limits=ExtractionLimits(page_timeout_s=0.05), settings=NO_OCR)
