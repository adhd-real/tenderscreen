"""OCR one PDF page with Tesseract. ``ocr_page`` runs inside a sandboxed worker.

Pages are rendered with PDFium (pypdfium2, BSD/Apache), never poppler, then
passed to the ``tesseract`` command-line program. Tesseract runs single-threaded
(OMP_THREAD_LIMIT=1) because pages already run in parallel across processes,
and it gets a minimal environment: it needs nothing secret.
"""

import ctypes
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Final

import pypdfium2 as pdfium

from tenderscreen.documents.errors import OcrUnavailableError
from tenderscreen.documents.sandbox import Json

POINTS_PER_INCH: Final = 72
_VERSION_TIMEOUT_S: Final = 30
_PR_SET_PDEATHSIG: Final = 1  # from <linux/prctl.h>
_LIBC: Final = ctypes.CDLL(None, use_errno=True) if sys.platform == "linux" else None


@dataclass(frozen=True, slots=True)
class Tesseract:
    path: str
    version: str
    languages: frozenset[str]


# Successful lookups only: if Tesseract is installed later, the next call finds it.
_found: dict[str, Tesseract] = {}


def find_tesseract(binary: str = "tesseract") -> Tesseract | None:
    """The installed Tesseract, its version and languages; None if it is not usable."""
    if binary in _found:
        return _found[binary]
    path = shutil.which(binary)
    if path is None:
        return None
    env = _tesseract_env()
    try:
        version = subprocess.run(  # noqa: S603 - our resolved binary, fixed arguments
            [path, "--version"], capture_output=True, text=True, timeout=_VERSION_TIMEOUT_S, env=env
        )
        languages = subprocess.run(  # noqa: S603 - our resolved binary, fixed arguments
            [path, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_S,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    version_lines = (version.stdout or version.stderr).strip().splitlines()
    if version.returncode != 0 or languages.returncode != 0 or not version_lines:
        return None
    # --list-langs prints a header line ("List of available languages ...") first.
    langs = frozenset(line.strip() for line in languages.stdout.splitlines()[1:] if line.strip())
    _found[binary] = Tesseract(path=path, version=version_lines[0], languages=langs)
    return _found[binary]


def require_tesseract(languages: str, binary: str = "tesseract") -> Tesseract:
    """The installed Tesseract, or a clear error naming what is missing."""
    tesseract = find_tesseract(binary)
    if tesseract is None:
        raise OcrUnavailableError(f"the '{binary}' program was not found or does not run")
    missing = sorted(set(languages.split("+")) - tesseract.languages)
    if missing:
        raise OcrUnavailableError(
            f"Tesseract language data missing for {', '.join(missing)} "
            f"(installed: {', '.join(sorted(tesseract.languages)) or 'none'})"
        )
    return tesseract


def _tesseract_env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", ""), "OMP_THREAD_LIMIT": "1"}
    if "TESSDATA_PREFIX" in os.environ:
        env["TESSDATA_PREFIX"] = os.environ["TESSDATA_PREFIX"]
    return env


def _die_with_parent(parent_pid: int) -> None:
    """Run in the Tesseract child before exec: be killed if the worker dies.

    When a worker is killed (a page overran its time limit), its Tesseract would
    otherwise keep running. Linux only; elsewhere the worker's own subprocess
    timeout still applies. If the worker already died before this took effect,
    exit at once.
    """
    if _LIBC is not None:
        _LIBC.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
    if os.getppid() != parent_pid:
        os._exit(1)


@dataclass(frozen=True, slots=True)
class OcrTask:
    """Everything a worker process needs to OCR one page (must be picklable)."""

    pdf_path: str
    page_index: int  # 0-based
    tesseract_path: str
    languages: str
    dpi: int
    max_pixels: int
    timeout_s: float
    work_dir: str  # an empty folder the parent created and will delete


@dataclass(frozen=True, slots=True)
class OcrOutcome:
    """A worker's result. Failures are reported here, not raised across processes."""

    page_index: int
    text: str = ""
    confidence: float | None = None
    error: str | None = None
    timed_out: bool = False
    worker_pid: int = 0


def outcome_from_json(value: Json) -> OcrOutcome:
    """Validate a worker's answer (it parsed untrusted data); ``ValueError`` if off."""
    keys = {"page_index", "text", "confidence", "error", "timed_out", "worker_pid"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("OCR answer has the wrong keys")
    index, text, confidence = value["page_index"], value["text"], value["confidence"]
    error, timed_out, pid = value["error"], value["timed_out"], value["worker_pid"]
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("OCR answer has a bad page index")
    if not isinstance(text, str) or not isinstance(timed_out, bool):
        raise ValueError("OCR answer has a bad text or timeout flag")
    if error is not None and not isinstance(error, str):
        raise ValueError("OCR answer has a bad error")
    if not isinstance(pid, int) or isinstance(pid, bool):
        raise ValueError("OCR answer has a bad worker pid")
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            raise ValueError("OCR answer has a bad confidence")
        if not 0 <= confidence <= 100:
            raise ValueError("OCR answer has an impossible confidence")
        confidence = float(confidence)
    return OcrOutcome(index, text, confidence, error, timed_out, pid)


def render_scale(width_pt: float, height_pt: float, dpi: int, max_pixels: int) -> float:
    """PDFium scale factor for ``dpi``, reduced so the image stays under ``max_pixels``."""
    scale = dpi / POINTS_PER_INCH
    pixels = (width_pt * scale) * (height_pt * scale)
    if pixels > max_pixels:
        scale *= math.sqrt(max_pixels / pixels)
    return scale


def mean_word_confidence(tsv: str) -> float | None:
    """Mean confidence (0-100) of recognised words in Tesseract's TSV output."""
    lines = tsv.splitlines()
    if not lines:
        return None
    header = lines[0].split("\t")
    try:
        level_col, conf_col, text_col = (header.index(c) for c in ("level", "conf", "text"))
    except ValueError:
        return None
    confidences = []
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) != len(header) or cols[level_col] != "5" or not cols[text_col].strip():
            continue
        try:
            confidence = float(cols[conf_col])
        except ValueError:
            continue
        if 0 <= confidence <= 100:
            confidences.append(confidence)
    return sum(confidences) / len(confidences) if confidences else None


def _render_png(task: OcrTask, out: Path) -> None:
    document = pdfium.PdfDocument(task.pdf_path)
    try:
        page = document[task.page_index]
        try:
            width, height = page.get_size()
            scale = render_scale(width, height, task.dpi, task.max_pixels)
            bitmap = page.render(scale=scale, grayscale=True, may_draw_forms=False)
            try:
                # Recording the DPI stops Tesseract guessing the resolution.
                bitmap.to_pil().save(out, dpi=(task.dpi, task.dpi))
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()


def ocr_page(task: OcrTask) -> OcrOutcome:
    """Render one page and OCR it, within ``task.timeout_s`` seconds in total."""
    started = time.monotonic()
    pid = os.getpid()
    work = Path(task.work_dir)
    image = work / "page.png"
    try:
        _render_png(task, image)
    except (pdfium.PdfiumError, OSError) as exc:
        return OcrOutcome(task.page_index, error=f"rendering failed: {exc}", worker_pid=pid)
    remaining = task.timeout_s - (time.monotonic() - started)
    if remaining <= 0:
        return OcrOutcome(task.page_index, timed_out=True, worker_pid=pid)
    out_base = work / "out"
    command = [task.tesseract_path, str(image), str(out_base), "-l", task.languages]
    try:
        result = subprocess.run(  # noqa: S603 - our resolved binary and our own files
            [*command, "txt", "tsv"],
            capture_output=True,
            text=True,
            timeout=remaining,
            env=_tesseract_env(),
            # Safe here: the worker process runs a single thread.
            preexec_fn=partial(_die_with_parent, pid),
        )
    except subprocess.TimeoutExpired:
        return OcrOutcome(task.page_index, timed_out=True, worker_pid=pid)
    if result.returncode != 0:
        detail = result.stderr.strip()[-500:] or f"exit code {result.returncode}"
        return OcrOutcome(task.page_index, error=f"tesseract: {detail}", worker_pid=pid)
    try:
        text = out_base.with_suffix(".txt").read_text(encoding="utf-8")
        tsv = out_base.with_suffix(".tsv").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return OcrOutcome(task.page_index, error=f"reading tesseract output: {exc}", worker_pid=pid)
    return OcrOutcome(
        task.page_index,
        text=text.rstrip(),  # Tesseract ends each page with a form feed
        confidence=mean_word_confidence(tsv),
        worker_pid=pid,
    )


def ocr_page_json(task: OcrTask) -> Json:
    """``ocr_page`` with a JSON answer, for the sandboxed worker."""
    outcome: Json = asdict(ocr_page(task))
    return outcome
