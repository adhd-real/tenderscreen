"""Text of every page of a PDF: from its text layer where usable, else by OCR.

    pages = extract_pages(Path("data/manual/notice.pdf"))
    pages[0].page_number, pages[0].method, pages[0].quality, pages[0].text

Pages without a usable text layer are rendered with PDFium and OCR'd with
Tesseract (English plus Hindi by default) in parallel worker processes. All
parsing of the untrusted file happens in sandboxed child processes with a
memory cap and per-page time limits (see ``sandbox.py``).

Child processes are started with "spawn", which re-imports the calling program:
a script that calls this must keep its top-level code under
``if __name__ == "__main__":`` (``python -m ...`` entry points already do).
"""

import shutil
import tempfile
from pathlib import Path
from typing import Final

import pypdfium2.version

from tenderscreen.documents.errors import OcrFailedError, PageTimeoutError, WorkerStartError
from tenderscreen.documents.models import (
    ExtractionLimits,
    ExtractionMethod,
    ExtractionSettings,
    PageText,
    assess_quality,
)
from tenderscreen.documents.ocr import (
    OcrOutcome,
    OcrTask,
    find_tesseract,
    ocr_page_json,
    outcome_from_json,
    require_tesseract,
)
from tenderscreen.documents.sandbox import (
    ChildStartError,
    TaskFailedError,
    TaskTimeoutError,
    run_tasks,
)
from tenderscreen.documents.serialization import decode_pages, encode_pages
from tenderscreen.documents.textlayer import (
    TextLayerPage,
    check_file,
    has_usable_text_layer,
    read_text_layers,
)
from tenderscreen.store.cache import ResultCache
from tenderscreen.store.documents import sha256_file
from tenderscreen.store.records import CacheKey

OCR_STAGE: Final = "rendering and OCR"
# Each worker stops its own Tesseract at the page limit and reports it; the
# parent kills a worker that has not answered this much later (for example
# because rendering hangs).
WORKER_GRACE_S: Final = 2.0

# Result-cache step name and version. Bump EXTRACT_PAGES_VERSION whenever a code
# change could change extract_pages' output, so cached results are recomputed.
EXTRACT_PAGES_STEP: Final = "extract_pages"
EXTRACT_PAGES_VERSION: Final = "1"


def step_version(settings: ExtractionSettings) -> str:
    """Everything that can change the output: code, PDFium, Tesseract, settings.

    Settings that cannot change the output (workers, the Tesseract program's
    path, time and memory limits) are left out, so changing them keeps the cache.
    """
    tesseract = find_tesseract(settings.tesseract_binary)
    parts = [
        EXTRACT_PAGES_VERSION,
        f"pdfium={pypdfium2.version.PDFIUM_INFO}",
        f"tesseract={tesseract.version if tesseract else 'none'}",
        f"languages={settings.languages}",
        f"min_text_chars={settings.min_text_chars}",
        f"image_page_min_text_chars={settings.image_page_min_text_chars}",
        f"image_coverage_threshold={settings.image_coverage_threshold}",
        f"ocr_dpi={settings.ocr_dpi}",
        f"max_render_pixels={settings.max_render_pixels}",
        f"min_ocr_confidence={settings.min_ocr_confidence}",
        f"max_suspicious_share={settings.max_suspicious_share}",
    ]
    return ";".join(parts)


def extract_pages(
    path: Path | str,
    *,
    limits: ExtractionLimits | None = None,
    settings: ExtractionSettings | None = None,
    cache: ResultCache | None = None,
) -> list[PageText]:
    """One ``PageText`` per page, in page order (page numbers start at 1).

    Raises ``DocumentError`` subclasses: ``FileTooLargeError`` and
    ``TooManyPagesError`` (limits), ``CorruptDocumentError`` (unreadable, not a
    PDF, password-protected, crashed the reader or exceeded the memory cap),
    ``PageTimeoutError`` (a page over the time limit), ``OcrUnavailableError``
    (OCR needed but Tesseract or a language missing) and ``OcrFailedError``.
    ``FileNotFoundError`` if the path does not exist. The first failing page
    stops the whole document.

    With a ``cache`` (see ``tenderscreen.store``), results are stored under the
    file's sha256 and ``step_version(settings)``: the same document is never
    processed twice, and a damaged cache entry is recomputed, never trusted.
    Failures are not cached.
    """
    path = Path(path)
    limits = limits or ExtractionLimits()
    settings = settings or ExtractionSettings()
    if cache is None:
        return _extract(path, limits, settings)
    check_file(path, limits)  # the size limit applies before the file is read to hash it
    key = CacheKey(sha256_file(path), EXTRACT_PAGES_STEP, step_version(settings))
    return cache.get_or_compute(
        key,
        lambda: _extract(path, limits, settings),
        encode=encode_pages,
        decode=decode_pages,
    )


def _extract(path: Path, limits: ExtractionLimits, settings: ExtractionSettings) -> list[PageText]:
    """The uncached work of ``extract_pages``."""
    layers = read_text_layers(path, limits)
    to_ocr = [page for page in layers if not has_usable_text_layer(page, settings)]
    outcomes = _ocr_pages(path, to_ocr, limits, settings) if to_ocr else {}

    pages = []
    for layer in layers:
        outcome = outcomes.get(layer.page_number)
        if outcome is None:
            method, text, confidence = ExtractionMethod.TEXT_LAYER, layer.text, None
        else:
            method, text, confidence = ExtractionMethod.OCR, outcome.text, outcome.confidence
        quality = assess_quality(text, method, confidence, settings)
        pages.append(PageText(layer.page_number, text, method, quality, confidence))
    return pages


def _check_outcome(name: str, outcome: OcrOutcome, timeout_s: float) -> None:
    """Raise for a failed page as soon as its answer arrives (stops every worker)."""
    page_number = outcome.page_index + 1
    if outcome.timed_out:
        raise PageTimeoutError(name, page_number, timeout_s, OCR_STAGE)
    if outcome.error is not None:
        raise OcrFailedError(name, page_number, outcome.error)


def _ocr_pages(
    path: Path,
    pages: list[TextLayerPage],
    limits: ExtractionLimits,
    settings: ExtractionSettings,
) -> dict[int, OcrOutcome]:
    """OCR outcomes keyed by 1-based page number; raises on the first failed page."""
    tesseract = require_tesseract(settings.languages, settings.tesseract_binary)
    work_root = Path(tempfile.mkdtemp(prefix="tenderscreen-ocr-"))
    try:
        tasks = []
        for page in pages:
            work_dir = work_root / f"page-{page.page_number}"
            work_dir.mkdir()
            tasks.append(
                OcrTask(
                    pdf_path=str(path),
                    page_index=page.page_number - 1,
                    tesseract_path=tesseract.path,
                    languages=settings.languages,
                    dpi=settings.ocr_dpi,
                    max_pixels=settings.max_render_pixels,
                    timeout_s=limits.page_timeout_s,
                    work_dir=str(work_dir),
                )
            )
        try:
            outcomes = run_tasks(
                ocr_page_json,
                tasks,
                decode=outcome_from_json,
                workers=settings.worker_count(len(tasks)),
                timeout_s=limits.page_timeout_s + WORKER_GRACE_S,
                memory_limit=limits.max_memory_bytes,
                on_result=lambda _, outcome: _check_outcome(
                    path.name, outcome, limits.page_timeout_s
                ),
            )
        except TaskTimeoutError as exc:
            page_number = tasks[exc.index].page_index + 1
            raise PageTimeoutError(
                path.name, page_number, limits.page_timeout_s, OCR_STAGE
            ) from exc
        except TaskFailedError as exc:
            raise OcrFailedError(path.name, tasks[exc.index].page_index + 1, exc.detail) from exc
        except ChildStartError as exc:
            raise WorkerStartError(str(exc)) from exc
        return {outcome.page_index + 1: outcome for outcome in outcomes}
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
