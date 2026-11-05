from pathlib import Path

import pytest

from tenderscreen.datacheck import inspect
from tenderscreen.datacheck.inspect import (
    FileFacts,
    _listed,
    counts_from_json,
    html_language,
    html_letter_counts,
    html_text,
    inspect_file,
)
from tenderscreen.documents import (
    ExtractionLimits,
    ExtractionMethod,
    PageQuality,
    PageText,
)
from tenderscreen.documents.errors import OcrUnavailableError
from tenderscreen.documents.ocr import Tesseract
from tenderscreen.documents.sandbox import (
    ChildStartError,
    Json,
    TaskFailedError,
    TaskTimeoutError,
)
from tenderscreen.store import open_store
from tests.documents.pdf_factory import (
    ENGLISH_NOTICE,
    HINDI_NOTICE,
    BlankPage,
    ScannedPage,
    TextPage,
    build_pdf,
    scan_image,
)

LONG_SCAN = ScannedPage(ENGLISH_NOTICE * 2)

LONG_HINDI = ("निविदा सूचना: सड़क निर्माण कार्य हेतु निविदा आमंत्रित की जाती है",) * 3


def test_text_pdf(tmp_path: Path) -> None:
    facts = inspect_file(build_pdf(tmp_path / "t.pdf", [TextPage(), TextPage()]), ocr=False)
    assert facts == FileFacts("pdf", "text layer", "2", "english")


def test_hindi_text_pdf(tmp_path: Path, devanagari_font: str) -> None:
    path = build_pdf(tmp_path / "h.pdf", [TextPage(LONG_HINDI, font_path=devanagari_font)])
    assert inspect_file(path, ocr=False).language == "hindi"


def test_scanned_pdf_without_ocr_leaves_language_blank(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "s.pdf", [ScannedPage(), BlankPage()])
    facts = inspect_file(path, ocr=False)
    assert (facts.file_format, facts.text_layer_or_scanned, facts.page_count) == (
        "pdf",
        "scanned",
        "2",
    )
    assert facts.language == ""
    assert facts.problem == "language not checked: scanned pages need OCR"


def test_mixed_pdf(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "m.pdf", [TextPage(), ScannedPage()])
    assert inspect_file(path, ocr=False).text_layer_or_scanned == "mixed"


def test_corrupt_pdf_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 4)
    facts = inspect_file(path, ocr=False)
    assert facts.file_format == "pdf"
    assert "not a readable PDF" in facts.problem
    assert facts.page_count == ""


def test_oversized_file_is_not_read(tmp_path: Path) -> None:
    path = build_pdf(tmp_path / "big.pdf", [TextPage()])
    facts = inspect_file(path, ocr=False, limits=ExtractionLimits(max_file_bytes=10))
    assert facts == FileFacts(problem="larger than 10 bytes; not inspected")


def test_html_page(tmp_path: Path) -> None:
    path = tmp_path / "aoc.html"
    path.write_text(
        "<!DOCTYPE html><html><head><style>body{}</style><script>var x = 'हिंदी हिंदी';</script>"
        "</head><body><h1>Award of Contract</h1><p>Name of successful bidder: Synthetic "
        "Builders. Contract value Rs. 12,34,567</p></body></html>",
        encoding="utf-8",
    )
    assert inspect_file(path, ocr=False) == FileFacts("html", "text (html)", "", "english")


def test_html_text_skips_scripts_and_styles() -> None:
    html = b"<p>Visible &amp; kept</p><script>hidden()</script><style>.x{}</style><p>too</p>"
    assert html_text(html) == "Visible & kept too"


def test_image_file(tmp_path: Path) -> None:
    path = tmp_path / "scan.png"
    scan_image(["AWARD"]).save(path)
    assert inspect_file(path, ocr=False) == FileFacts("png", "scanned (image)", "1")


@pytest.mark.parametrize(
    ("content", "expected"),
    [(b"PK\x03\x04rest", "zip (docx/xlsx/odt?)"), (b"\x00\x01\x02", "unknown")],
)
def test_other_formats(tmp_path: Path, content: bytes, expected: str) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(content)
    assert inspect_file(path, ocr=False) == FileFacts(expected)


def test_prefill_maps_to_columns() -> None:
    facts = FileFacts("pdf", "scanned", "3", "hindi", "ignored")
    assert facts.prefill() == {
        "file_format": "pdf",
        "text_layer_or_scanned": "scanned",
        "page_count": "3",
        "language": "hindi",
    }


def test_hindi_notice_constant_is_hindi() -> None:
    from tenderscreen.datacheck.language import language_of

    assert language_of(" ".join(HINDI_NOTICE * 3)) == "hindi"


def test_unreadable_file_is_a_problem_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "locked.pdf"
    path.write_bytes(b"%PDF-1.7")

    def denied(self: Path) -> bytes:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", denied)
    assert inspect_file(path, ocr=False) == FileFacts(
        problem="could not be read: Permission denied"
    )


def test_vanished_file_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    facts = inspect_file(tmp_path / "gone.pdf", ocr=False)
    assert facts == FileFacts(problem="could not be read: No such file or directory")


# The OCR path (moved from test_ocr_path.py, which mirrored no module).


@pytest.mark.slow
def test_scanned_pdf_language_comes_from_ocr(tmp_path: Path, tesseract: Tesseract) -> None:
    path = build_pdf(tmp_path / "scan.pdf", [LONG_SCAN])
    with open_store(tmp_path / "raw") as store:
        facts = inspect_file(path, ocr=True, cache=store.cache)
    assert (facts.text_layer_or_scanned, facts.language, facts.problem) == (
        "scanned",
        "english",
        "",
    )


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


# Found in review.


def ocr_page(number: int, quality: PageQuality) -> PageText:
    text = "Notice inviting tender " * 3
    return PageText(number, text, ExtractionMethod.OCR, quality, ocr_confidence=50.0)


def test_low_quality_ocr_leaves_the_language_for_a_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_pdf(tmp_path / "scan.pdf", [ScannedPage(), ScannedPage(), ScannedPage()])
    pages = [
        ocr_page(1, PageQuality.GOOD),
        ocr_page(2, PageQuality.LOW),
        ocr_page(3, PageQuality.LOW),
    ]
    monkeypatch.setattr("tenderscreen.datacheck.inspect.extract_pages", lambda *a, **k: pages)
    facts = inspect_file(path, ocr=True)
    assert facts.language == ""
    assert facts.problem == (
        "language not filled: OCR quality is low on page 2, 3; check it by eye"
    )
    assert (facts.text_layer_or_scanned, facts.page_count) == ("scanned", "3")


def test_good_ocr_fills_the_language(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = build_pdf(tmp_path / "scan.pdf", [ScannedPage(), TextPage()])
    pages = [
        ocr_page(1, PageQuality.GOOD),
        PageText(2, "Notice inviting tender " * 3, ExtractionMethod.TEXT_LAYER, PageQuality.LOW),
    ]
    monkeypatch.setattr("tenderscreen.datacheck.inspect.extract_pages", lambda *a, **k: pages)
    assert inspect_file(path, ocr=True).language == "english"  # only OCR pages are doubted


def test_listed_shortens_long_page_lists() -> None:
    assert _listed([2]) == "2"
    assert _listed([2, 5, 9]) == "2, 5, 9"
    assert _listed(list(range(1, 11))) == "1, 2, 3, 4, 5, 6, 7, 8, 9, 10"
    assert _listed(list(range(1, 13))) == "1, 2, 3, 4, 5, 6, 7, 8, 9, 10, ..."


def test_pdf_with_no_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PDFium refuses to open a PDF with no pages, so the reader is stubbed here."""
    path = build_pdf(tmp_path / "t.pdf", [TextPage()])
    monkeypatch.setattr("tenderscreen.datacheck.inspect.read_text_layers", lambda *a: [])
    assert inspect_file(path, ocr=False) == FileFacts("pdf", "", "0", "")


@pytest.mark.parametrize(
    ("html", "text"),
    [
        (b"<P>Upper</P><SCRIPT>x()</SCRIPT>case", "Upper case"),
        (b"<noscript><style>a{}</style>hidden</noscript>shown", "shown"),
        (b"</script>stray end tag", "stray end tag"),
        (b"<p>before<script>never closed", "before"),
        (b"<p <b>odd</b> markup", "odd markup"),
        (b"Tata &amp; Sons &#8377; &#x20B9;", "Tata & Sons \u20b9 \u20b9"),
        (b"\xff\xfe<p>bad bytes</p>", "\ufffd\ufffd bad bytes"),
        (b"", ""),
    ],
)
def test_html_text_edges(html: bytes, text: str) -> None:
    assert html_text(html) == text


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        # Windows-1252 text is not UTF-8, so its bytes alone do not say HTML.
        ("aoc.html", "<p>Award to Caf\xe9 Builders for the road works contract</p>", "html"),
        ("aoc.HTM", "Award of contract: Synthetic Builders, road works, Ward 4", "html"),
        ("notes.txt", "Award of contract: Synthetic Builders, road works, Ward 4", "text"),
    ],
)
def test_html_recognised_by_its_name_when_the_bytes_do_not_say(
    tmp_path: Path, name: str, content: str, expected: str
) -> None:
    path = tmp_path / name
    path.write_bytes(content.encode("cp1252"))
    facts = inspect_file(path, ocr=False)
    assert facts.file_format == expected
    if expected == "html":
        assert (facts.text_layer_or_scanned, facts.language) == ("text (html)", "english")


def test_binary_file_named_html_is_not_html(tmp_path: Path) -> None:
    path = tmp_path / "fake.html"
    path.write_bytes(b"\x00\x01binary" * 10)
    assert inspect_file(path, ocr=False) == FileFacts("unknown")


def test_html_letter_counts_reads_only_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "page.html"
    path.write_text("<p>abc</p><p>निविदा</p>" + "<p>xyz</p>" * 100, encoding="utf-8")
    assert html_letter_counts(str(path)) == [6, 303, 0]
    monkeypatch.setattr(inspect, "MAX_HTML_BYTES", 10)
    assert html_letter_counts(str(path)) == [0, 3, 0]


@pytest.mark.parametrize(
    "value",
    [[1, 2], [1, 2, 3, 4], [1, -2, 3], [1, True, 3], [1.0, 2, 3], {"a": 1}, None, "1,2,3"],
)
def test_counts_from_json_rejects_anything_but_three_counts(value: Json) -> None:
    with pytest.raises(ValueError, match="three letter counts"):
        counts_from_json(value)


def test_counts_from_json() -> None:
    assert counts_from_json([0, 5, 2]) == (0, 5, 2)


def test_html_language_runs_in_a_worker(tmp_path: Path) -> None:
    path = tmp_path / "hindi.html"
    path.write_text("<p>" + " ".join(HINDI_NOTICE * 3) + "</p>", encoding="utf-8")
    assert html_language(path, ExtractionLimits()) == ("hindi", "")


@pytest.mark.parametrize(
    ("error", "problem"),
    [
        (TaskTimeoutError(0), "language not checked: reading the page took over 60 s"),
        (
            TaskFailedError(0, "MemoryError: "),
            "language not checked: the page could not be read (MemoryError: )",
        ),
        (ChildStartError("no processes left"), "language not checked: no processes left"),
    ],
)
def test_html_language_problems_are_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, problem: str
) -> None:
    def failing(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr("tenderscreen.datacheck.inspect.run_tasks", failing)
    path = tmp_path / "page.html"
    path.write_text("<html><body>Award</body></html>", encoding="utf-8")
    assert html_language(path, ExtractionLimits()) == ("", problem)
    assert inspect_file(path, ocr=False) == FileFacts("html", "text (html)", problem=problem)


@pytest.mark.slow
def test_html_parse_is_stopped_after_the_time_limit(tmp_path: Path) -> None:
    path = tmp_path / "slow.html"
    path.write_text("<p>Award of contract</p>" * 100_000, encoding="utf-8")  # 2.4 MB to parse
    limits = ExtractionLimits(page_timeout_s=0.001)
    language, problem = html_language(path, limits)
    assert (language, problem) == ("", "language not checked: reading the page took over 0.001 s")
