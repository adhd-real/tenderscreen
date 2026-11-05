"""Read each PDF page's embedded text layer with PDFium (pypdfium2), without OCR.

Parsing happens in a sandboxed child process (see ``sandbox.py``): a memory cap,
and a deadline for opening the file and for every page, enforced by the parent,
which kills the child if it overruns. A malicious PDF (a "PDF bomb" of nested
forms, a parser crash) can therefore only fail cleanly, never hang or crash the
caller.

Also measures how much of each page is covered by images, so a scanned page that
carries a small text overlay (a digital-signature stamp, a scanner watermark) is
still recognised as a scan.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Final, TypeGuard

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from tenderscreen.documents.errors import (
    CorruptDocumentError,
    FileTooLargeError,
    PageTimeoutError,
    TooManyPagesError,
    WorkerStartError,
)
from tenderscreen.documents.models import ExtractionLimits, ExtractionSettings, meaningful_chars
from tenderscreen.documents.sandbox import (
    Child,
    ChildDiedError,
    ChildProtocolError,
    ChildStartError,
    ChildTimeoutError,
    Json,
    send_json,
    start_child,
)

PDF_MAGIC: Final = b"%PDF-"
# The PDF header may follow up to 1 KB of junk; readers accept that, so do we.
HEADER_SEARCH_BYTES: Final = 1024
# Nesting depth and number of image/form objects examined for image coverage.
MAX_FORM_DEPTH: Final = 15
MAX_COVERAGE_OBJECTS: Final = 10_000


@dataclass(frozen=True, slots=True)
class TextLayerPage:
    """What a page's embedded text layer holds."""

    page_number: int  # 1-based
    text: str
    meaningful_chars: int
    image_coverage: float  # 0.0-1.0 share of the page area covered by images


def clean_text(raw: str) -> str:
    """Normalise line endings and drop NUL characters; otherwise keep text verbatim."""
    return raw.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


def check_file(path: Path, limits: ExtractionLimits) -> None:
    """Refuse oversized, empty or non-PDF files before any parser touches them."""
    size = path.stat().st_size
    if size > limits.max_file_bytes:
        raise FileTooLargeError(path.name, size, limits.max_file_bytes)
    if size == 0:
        raise CorruptDocumentError(path.name, "the file is empty")
    with path.open("rb") as handle:
        head = handle.read(HEADER_SEARCH_BYTES)
    if PDF_MAGIC not in head:
        raise CorruptDocumentError(path.name, "no %PDF- header in the first 1 KB")


@contextmanager
def open_pdf(path: Path, limits: ExtractionLimits) -> Iterator[pdfium.PdfDocument]:
    """Open a checked PDF in *this* process, enforce the page limit, and always close it.

    Only for code already running in a sandboxed child (or tests): parsing an
    untrusted PDF in the main process has no time or memory bound.
    """
    check_file(path, limits)
    try:
        document = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        raise CorruptDocumentError(path.name, str(exc)) from exc
    try:
        pages = len(document)
        if pages > limits.max_pages:
            raise TooManyPagesError(path.name, pages, limits.max_pages)
        yield document
    finally:
        document.close()


def image_coverage(page: pdfium.PdfPage) -> float:
    """Share of the visible page area covered by images, in page coordinates.

    Images inside Form XObjects report bounds in the form's own coordinates, so
    they are mapped through every enclosing form's matrix; everything is clipped
    to the page's visible box (media box intersected with crop box), which need
    not start at (0, 0). Overlapping images may double count; the result is capped.
    """
    left, bottom, right, top = page.get_bbox()
    area = (right - left) * (top - bottom)
    if area <= 0:
        return 0.0
    covered = 0.0
    forms: list[pdfium.PdfMatrix] = []  # page-space matrix of each enclosing form, by level
    objects = page.get_objects(
        filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE, pdfium_c.FPDF_PAGEOBJ_FORM], max_depth=MAX_FORM_DEPTH
    )
    for count, obj in enumerate(objects):
        if count >= MAX_COVERAGE_OBJECTS:
            break
        del forms[obj.level :]  # keep only this object's ancestors
        if obj.type == pdfium_c.FPDF_PAGEOBJ_FORM:
            matrix = obj.get_matrix()
            forms.append(matrix.multiply(forms[-1]) if forms else matrix)
            continue
        x0, y0, x1, y1 = obj.get_bounds()
        if forms:
            x0, y0, x1, y1 = forms[-1].on_rect(x0, y0, x1, y1)
        width = max(0.0, min(x1, right) - max(x0, left))
        height = max(0.0, min(y1, top) - max(y0, bottom))
        covered += width * height
    return min(1.0, covered / area)


def read_page(document: pdfium.PdfDocument, index: int) -> TextLayerPage:
    """Text layer and image coverage of one page (0-based ``index``)."""
    page = document[index]
    try:
        textpage = page.get_textpage()
        try:
            text = clean_text(textpage.get_text_bounded())
        finally:
            textpage.close()
        return TextLayerPage(index + 1, text, meaningful_chars(text), image_coverage(page))
    finally:
        page.close()


def text_layer_child(conn: Connection, path: str, max_pages: int) -> None:
    """Child side: send ["count", n], then ["page", {...}] per page, or ["error", why]."""
    try:
        document = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        send_json(conn, ["error", str(exc)])
        return
    try:
        pages = len(document)
        send_json(conn, ["count", pages])
        if pages > max_pages:
            return
        for index in range(pages):
            try:
                page = read_page(document, index)
            except pdfium.PdfiumError as exc:
                send_json(conn, ["error", f"page {index + 1}: {exc}"])
                return
            send_json(conn, ["page", asdict(page)])
    finally:
        document.close()


def _is_int(value: Json) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def page_from_json(value: Json, page_number: int) -> TextLayerPage:
    """Validate one page message from the child; ``ValueError`` if anything is off."""
    keys = {"page_number", "text", "meaningful_chars", "image_coverage"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("page message has the wrong keys")
    number, text = value["page_number"], value["text"]
    chars, coverage = value["meaningful_chars"], value["image_coverage"]
    if not _is_int(number) or number != page_number or not isinstance(text, str):
        raise ValueError("page message has the wrong page number or text")
    if not _is_int(chars) or chars != meaningful_chars(text):
        raise ValueError("page message has a wrong character count")
    if isinstance(coverage, bool) or not isinstance(coverage, int | float):
        raise ValueError("page message has no image coverage")
    if not 0 <= coverage <= 1:
        raise ValueError("page message has an impossible image coverage")
    return TextLayerPage(number, text, chars, float(coverage))


def _message(child: Child, timeout_s: float) -> tuple[str, Json]:
    message = child.receive(timeout_s)
    if not (isinstance(message, list) and len(message) == 2 and isinstance(message[0], str)):
        raise ChildProtocolError("unexpected message from the PDF reader")
    return message[0], message[1]


def read_text_layers(path: Path, limits: ExtractionLimits | None = None) -> list[TextLayerPage]:
    """The text layer of every page, in order, parsed in a sandboxed child process.

    Raises ``FileTooLargeError``, ``TooManyPagesError``, ``CorruptDocumentError``
    (unreadable, crashed the reader, or needed more than the memory cap) and
    ``PageTimeoutError`` (opening, or any one page, over the time limit).
    """
    limits = limits or ExtractionLimits()
    check_file(path, limits)
    try:
        child = start_child(
            text_layer_child, (str(path), limits.max_pages), limits.max_memory_bytes
        )
    except ChildStartError as exc:
        raise WorkerStartError(str(exc)) from exc
    page_number: int | None = None
    stage = "opening the PDF"
    try:
        kind, value = _message(child, limits.page_timeout_s)
        if kind == "error":
            raise CorruptDocumentError(path.name, str(value))
        if kind != "count" or type(value) is not int or value < 0:
            raise ChildProtocolError("unexpected page count from the PDF reader")
        if value > limits.max_pages:
            raise TooManyPagesError(path.name, value, limits.max_pages)
        pages = []
        stage = "reading the text layer"
        for page_number in range(1, value + 1):
            kind, data = _message(child, limits.page_timeout_s)
            if kind == "error":
                raise CorruptDocumentError(path.name, str(data))
            if kind != "page":
                raise ChildProtocolError("unexpected message from the PDF reader")
            pages.append(page_from_json(data, page_number))
        return pages
    except ChildTimeoutError:
        raise PageTimeoutError(path.name, page_number, limits.page_timeout_s, stage) from None
    except ChildDiedError as exc:
        raise CorruptDocumentError(
            path.name,
            f"{exc} while {stage}; the file may be malformed, or need more memory than "
            "ExtractionLimits.max_memory_bytes",
        ) from exc
    except (ChildProtocolError, ValueError) as exc:
        raise CorruptDocumentError(path.name, f"the PDF reader gave invalid output: {exc}") from exc
    finally:
        child.stop()


def has_usable_text_layer(page: TextLayerPage, settings: ExtractionSettings) -> bool:
    """True if the page's own text can be used instead of OCR."""
    if page.meaningful_chars < settings.min_text_chars:
        return False
    looks_scanned = page.image_coverage >= settings.image_coverage_threshold
    return not (looks_scanned and page.meaningful_chars < settings.image_page_min_text_chars)
