"""PDF page text: the embedded text layer where usable, otherwise Tesseract OCR."""

from tenderscreen.documents.errors import (
    CorruptDocumentError,
    DocumentError,
    DocumentTimeoutError,
    FileTooLargeError,
    OcrFailedError,
    OcrUnavailableError,
    PageTimeoutError,
    TooManyPagesError,
    WorkerStartError,
)
from tenderscreen.documents.extract import extract_pages
from tenderscreen.documents.models import (
    ExtractionLimits,
    ExtractionMethod,
    ExtractionSettings,
    PageQuality,
    PageText,
)

__all__ = [
    "CorruptDocumentError",
    "DocumentError",
    "DocumentTimeoutError",
    "ExtractionLimits",
    "ExtractionMethod",
    "ExtractionSettings",
    "FileTooLargeError",
    "OcrFailedError",
    "OcrUnavailableError",
    "PageQuality",
    "PageText",
    "PageTimeoutError",
    "TooManyPagesError",
    "WorkerStartError",
    "extract_pages",
]
