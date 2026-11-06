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

import dataclasses
import shutil
import tempfile
import time
from pathlib import Path
from typing import Final

import pypdfium2.version

from tenderscreen.documents.errors import (
    ContentMismatchError,
    DocumentTimeoutError,
    OcrFailedError,
    PageTimeoutError,
    TooManyPagesError,
    WorkerStartError,
)
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
    DeadlineError,
    TaskFailedError,
    TaskTimeoutError,
    clip,
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
from tenderscreen.store.records import CacheKey, check_sha256

OCR_STAGE: Final = "rendering and OCR"
# Each worker stops its own Tesseract at the page limit and reports it; the
# parent kills a worker that has not answered this much later (for example
# because rendering hangs).
WORKER_GRACE_S: Final = 2.0

# Result-cache step name and version. Bump EXTRACT_PAGES_VERSION whenever a code
# change could change extract_pages' output, so cached results are recomputed.
EXTRACT_PAGES_STEP: Final = "extract_pages"
EXTRACT_PAGES_VERSION: Final = "1"


# Settings that cannot change extract_pages' output, so they are left out of the
# cache key. Every other ExtractionSettings field is included automatically.
SETTINGS_NOT_IN_VERSION: Final = frozenset({"workers", "tesseract_binary"})


def step_version(settings: ExtractionSettings) -> str:
    """Everything that can change the output: code, PDFium, Tesseract, settings.

    Time and memory limits and the settings in ``SETTINGS_NOT_IN_VERSION`` are
    left out, so changing them keeps the cache.
    """
    tesseract = find_tesseract(settings.tesseract_binary)
    tesseract_id = f"{tesseract.version}#{tesseract.fingerprint}" if tesseract else "none"
    parts = [
        EXTRACT_PAGES_VERSION,
        f"pypdfium2={pypdfium2.version.PYPDFIUM_INFO}",
        f"pdfium={pypdfium2.version.PDFIUM_INFO}",
        f"tesseract={tesseract_id}",
    ]
    for field in dataclasses.fields(settings):
        if field.name not in SETTINGS_NOT_IN_VERSION:
            parts.append(f"{field.name}={getattr(settings, field.name)}")
    return ";".join(parts)


def extract_pages(
    path: Path | str,
    *,
    limits: ExtractionLimits | None = None,
    settings: ExtractionSettings | None = None,
    cache: ResultCache | None = None,
    expected_sha256: str | None = None,
) -> list[PageText]:
    """One ``PageText`` per page, in page order (page numbers start at 1).

    Raises ``DocumentError`` subclasses: ``FileTooLargeError`` and
    ``TooManyPagesError`` (limits), ``CorruptDocumentError`` (unreadable, not a
    PDF, password-protected, crashed the reader or exceeded the memory cap),
    ``PageTimeoutError`` (a page over the time limit), ``OcrUnavailableError``
    (OCR needed but Tesseract or a language missing) and ``OcrFailedError``.
    ``DocumentTimeoutError`` (the whole document over ``max_document_s``),
    ``WorkerStartError``, and ``FileNotFoundError`` if the path does not exist.
    The first failing page stops the whole document.

    With a ``cache`` (see ``tenderscreen.store``), results are stored under the
    file's sha256 and ``step_version(settings)``: the same document is never
    processed twice, and a damaged cache entry is recomputed, never trusted.
    Failures are not cached. A failure of the cache's database is a
    ``StoreError`` (from ``tenderscreen.store``).

    ``expected_sha256`` (for example a stored document's) is the sha256 the
    file must have: ``ContentMismatchError`` if it has another, before or
    after the work, so pages are never credited to the wrong document.
    """
    path = Path(path)
    limits = limits or ExtractionLimits()
    settings = settings or ExtractionSettings()
    if expected_sha256 is not None:
        check_sha256(expected_sha256)
    elif cache is None:
        return _extract(path, limits, settings)
    check_file(path, limits)  # the size limit applies before the file is read to hash it
    sha256 = sha256_file(path)
    _require(path, expected_sha256, sha256)
    if cache is None:
        pages = _extract(path, limits, settings)
        _require(path, expected_sha256, _current_sha256(path))  # changed while being read?
        return pages
    changed = False

    def unchanged(_: list[PageText]) -> bool:
        # If the file changed (or vanished) while it was being read, its pages are
        # not the pages of ``sha256``: never cache them under that hash.
        nonlocal changed
        changed = _current_sha256(path) != sha256
        return not changed

    pages = cache.get_or_compute(
        CacheKey(sha256, EXTRACT_PAGES_STEP, step_version(settings)),
        lambda: _extract(path, limits, settings),
        encode=encode_pages,
        decode=decode_pages,
        keep=unchanged,
    )
    if changed:
        _require(path, expected_sha256, _current_sha256(path))
    if len(pages) > limits.max_pages:  # a cached result must respect today's limits too
        raise TooManyPagesError(path.name, len(pages), limits.max_pages)
    return pages


def _current_sha256(path: Path) -> str | None:
    """The file's sha256 now, or None if it can no longer be read."""
    try:
        return sha256_file(path)
    except OSError:
        return None


def _require(path: Path, expected_sha256: str | None, actual_sha256: str | None) -> None:
    """``ContentMismatchError`` unless the file has the expected sha256 (if any)."""
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise ContentMismatchError(path.name, expected_sha256, actual_sha256)


def _extract(path: Path, limits: ExtractionLimits, settings: ExtractionSettings) -> list[PageText]:
    """The uncached work of ``extract_pages``."""
    deadline = time.monotonic() + limits.max_document_s
    layers = read_text_layers(path, limits, deadline=deadline)
    to_ocr = [page for page in layers if not has_usable_text_layer(page, settings)]
    outcomes = _ocr_pages(path, to_ocr, limits, settings, deadline) if to_ocr else {}

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


def _check_outcome(name: str, task: OcrTask, outcome: OcrOutcome, timeout_s: float) -> None:
    """Raise for a failed page as soon as its answer arrives (stops every worker).

    The page is the one the parent asked for, never the one the worker claims:
    a worker parses untrusted data, and a wrong page would mean a wrong
    ``source_page`` citation later (CLAUDE.md rule 3).
    """
    page_number = task.page_index + 1
    if outcome.page_index != task.page_index:
        detail = f"the worker answered for page {outcome.page_index + 1} instead"
        raise OcrFailedError(name, page_number, detail)
    if outcome.timed_out:
        raise PageTimeoutError(name, page_number, timeout_s, OCR_STAGE)
    if outcome.error is not None:
        raise OcrFailedError(name, page_number, clip(outcome.error))


def _ocr_pages(
    path: Path,
    pages: list[TextLayerPage],
    limits: ExtractionLimits,
    settings: ExtractionSettings,
    deadline: float,
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
                on_result=lambda index, outcome: _check_outcome(
                    path.name, tasks[index], outcome, limits.page_timeout_s
                ),
                deadline=deadline,
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
        except DeadlineError as exc:
            raise DocumentTimeoutError(path.name, limits.max_document_s) from exc
        return {task.page_index + 1: outcome for task, outcome in zip(tasks, outcomes, strict=True)}
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
