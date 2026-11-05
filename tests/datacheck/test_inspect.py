from pathlib import Path

import pytest

from tenderscreen.datacheck.inspect import FileFacts, html_text, inspect_file
from tenderscreen.documents import ExtractionLimits
from tests.documents.pdf_factory import (
    HINDI_NOTICE,
    BlankPage,
    ScannedPage,
    TextPage,
    build_pdf,
    scan_image,
)

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
