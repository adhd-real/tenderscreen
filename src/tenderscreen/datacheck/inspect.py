"""Work out what can be known about one file in data/manual/ without a person.

File format (from the bytes), text layer or scanned and page count (for PDFs,
from the text layer alone, no OCR), and language (Devanagari vs Latin share of
the text; scanned pages are OCR'd for this unless OCR is switched off). Every
parse of a file's content runs in a sandboxed child process with a time limit.
"""

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Final

from tenderscreen.datacheck.language import language_from_counts, language_of, letter_counts
from tenderscreen.documents import (
    DocumentError,
    ExtractionLimits,
    ExtractionMethod,
    ExtractionSettings,
    PageQuality,
    extract_pages,
)
from tenderscreen.documents.sandbox import (
    ChildStartError,
    Json,
    TaskFailedError,
    TaskTimeoutError,
    run_tasks,
)
from tenderscreen.documents.textlayer import has_usable_text_layer, read_text_layers
from tenderscreen.store import ResultCache, StoreError
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
# The language of a web page is clear from its start; parsing stops here.
MAX_HTML_BYTES: Final = 5 * 1024 * 1024
# A page saved in a legacy encoding (not UTF-8) or starting with plain text is
# not recognised from its bytes; its name still says it is HTML.
HTML_SUFFIXES: Final = frozenset({".htm", ".html"})
MAX_LISTED_PAGES: Final = 10


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


def html_letter_counts(path: str) -> Json:
    """Worker side: letters by script in the visible text of the file's first 5 MB."""
    with open(path, "rb") as handle:
        content = handle.read(MAX_HTML_BYTES)
    return list(letter_counts(html_text(content)))


def counts_from_json(value: Json) -> tuple[int, int, int]:
    """The worker's answer as three letter counts; ``ValueError`` if it is anything else."""
    if isinstance(value, list) and len(value) == 3:
        devanagari, latin, other = value
        if (
            type(devanagari) is int
            and type(latin) is int
            and type(other) is int
            and min(devanagari, latin, other) >= 0
        ):
            return devanagari, latin, other
    raise ValueError("expected three letter counts")


def html_language(path: Path, limits: ExtractionLimits) -> tuple[str, str]:
    """(language, problem) for an HTML file, parsed in a sandboxed child with a time limit."""
    try:
        counts = run_tasks(
            html_letter_counts,
            [str(path)],
            decode=counts_from_json,
            workers=1,
            timeout_s=limits.page_timeout_s,
            memory_limit=limits.max_memory_bytes,
        )[0]
    except TaskTimeoutError:
        return "", f"language not checked: reading the page took over {limits.page_timeout_s:g} s"
    except TaskFailedError as exc:
        return "", f"language not checked: the page could not be read ({exc.detail})"
    except ChildStartError as exc:
        return "", f"language not checked: {exc}"
    return language_from_counts(*counts), ""


def _listed(numbers: list[int]) -> str:
    """Page numbers for a message: "2, 5, 9", shortened after ten."""
    shown = ", ".join(str(n) for n in numbers[:MAX_LISTED_PAGES])
    return shown + (", ..." if len(numbers) > MAX_LISTED_PAGES else "")


def inspect_pdf(
    path: Path, *, ocr: bool, cache: ResultCache | None, limits: ExtractionLimits
) -> FileFacts:
    settings = ExtractionSettings()
    try:
        layers = read_text_layers(path, limits)
    except DocumentError as exc:
        return FileFacts("pdf", problem=str(exc))
    except OSError as exc:  # removed or unreadable since it was listed
        return FileFacts("pdf", problem=f"could not be read: {exc.strerror or exc}")
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
    except StoreError as exc:  # e.g. the cache is locked by another run: skip, don't abort
        return FileFacts(**facts, problem=f"language not checked: the OCR cache failed: {exc}")
    except OSError as exc:
        return FileFacts(**facts, problem=f"language not checked: {exc.strerror or exc}")
    doubtful = [
        page.page_number
        for page in pages
        if page.method is ExtractionMethod.OCR and page.quality is PageQuality.LOW
    ]
    if doubtful:  # a misread script could look like another language: leave it to a person
        problem = f"language not filled: OCR quality is low on page {_listed(doubtful)}"
        return FileFacts(**facts, problem=problem + "; check it by eye")
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
    if (
        media_type in {"text/plain", "application/octet-stream"}
        and path.suffix.lower() in HTML_SUFFIXES
        and b"\x00" not in content[:1024]  # binary data, whatever its name says
    ):
        media_type = "text/html"
    file_format = FORMAT_NAMES.get(media_type, "unknown")
    if media_type == "application/pdf":
        return inspect_pdf(path, ocr=ocr, cache=cache, limits=limits)
    if media_type == "text/html":
        language, problem = html_language(path, limits)
        return FileFacts(file_format, "text (html)", language=language, problem=problem)
    if media_type.startswith("image/"):
        # A PNG or JPEG holds one page; a TIFF (or GIF) can hold several, and a
        # wrong pre-filled count is worse than a blank one.
        single = media_type in {"image/png", "image/jpeg"}
        return FileFacts(file_format, "scanned (image)", "1" if single else "")
    return FileFacts(file_format)
