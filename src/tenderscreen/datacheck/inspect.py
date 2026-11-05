"""Work out what can be known about one file in data/manual/ without a person.

File format (from the bytes), text layer or scanned and page count (for PDFs,
from the text layer alone, no OCR), and language (Devanagari vs Latin share of
the text; scanned pages are OCR'd for this unless OCR is switched off).
"""

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Final

from tenderscreen.datacheck.language import language_of
from tenderscreen.documents import (
    DocumentError,
    ExtractionLimits,
    ExtractionSettings,
    extract_pages,
)
from tenderscreen.documents.textlayer import has_usable_text_layer, read_text_layers
from tenderscreen.store import ResultCache
from tenderscreen.store.documents import sniff_content_type

FORMAT_NAMES: Final = {
    "application/pdf": "pdf",
    "text/html": "html",
    "application/xml": "xml",
    "text/plain": "text",
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/tiff": "tiff",
    "application/zip": "zip (docx/xlsx/odt?)",
    "application/x-ole-storage": "doc/xls",
}
_SKIP_TAGS: Final = frozenset({"script", "style", "noscript", "template"})


@dataclass(frozen=True, slots=True)
class FileFacts:
    file_format: str = ""
    text_layer_or_scanned: str = ""
    page_count: str = ""
    language: str = ""
    problem: str = ""  # what could not be worked out, and why

    def prefill(self) -> dict[str, str]:
        return {
            "file_format": self.file_format,
            "text_layer_or_scanned": self.text_layer_or_scanned,
            "page_count": self.page_count,
            "language": self.language,
        }


class _TextOnly(HTMLParser):
    """Collect visible text; never runs or fetches anything in the page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skipping += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def html_text(content: bytes) -> str:
    """Visible text of an HTML page (scripts and styles dropped)."""
    parser = _TextOnly()
    parser.feed(content.decode("utf-8", errors="replace"))
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def inspect_pdf(
    path: Path, *, ocr: bool, cache: ResultCache | None, limits: ExtractionLimits
) -> FileFacts:
    settings = ExtractionSettings()
    try:
        layers = read_text_layers(path, limits)
    except DocumentError as exc:
        return FileFacts("pdf", problem=str(exc))
    usable = [has_usable_text_layer(page, settings) for page in layers]
    status = "text layer" if all(usable) else "scanned" if not any(usable) else "mixed"
    facts = {"file_format": "pdf", "text_layer_or_scanned": status if layers else ""}
    facts["page_count"] = str(len(layers))
    if all(usable):
        return FileFacts(**facts, language=language_of("\n".join(p.text for p in layers)))
    if not ocr:
        return FileFacts(**facts, problem="language not checked: scanned pages need OCR")
    try:
        pages = extract_pages(path, limits=limits, settings=settings, cache=cache)
    except DocumentError as exc:
        return FileFacts(**facts, problem=f"language not checked: {exc}")
    return FileFacts(**facts, language=language_of("\n".join(p.text for p in pages)))


def inspect_file(
    path: Path,
    *,
    ocr: bool = True,
    cache: ResultCache | None = None,
    limits: ExtractionLimits | None = None,
) -> FileFacts:
    """Facts about one file; problems are reported in ``FileFacts.problem``, never raised."""
    limits = limits or ExtractionLimits()
    try:
        size = path.stat().st_size
        if size > limits.max_file_bytes:
            return FileFacts(problem=f"larger than {limits.max_file_bytes} bytes; not inspected")
        content = path.read_bytes()
    except OSError as exc:
        return FileFacts(problem=f"could not be read: {exc.strerror or exc}")
    media_type = sniff_content_type(content)
    file_format = FORMAT_NAMES.get(media_type, "unknown")
    if media_type == "application/pdf":
        return inspect_pdf(path, ocr=ocr, cache=cache, limits=limits)
    if media_type == "text/html":
        return FileFacts(file_format, "text (html)", language=language_of(html_text(content)))
    if media_type.startswith("image/"):
        return FileFacts(file_format, "scanned (image)", "1")
    return FileFacts(file_format)
