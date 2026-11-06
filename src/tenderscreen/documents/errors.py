"""Errors raised while reading a document. All derive from ``DocumentError``.

Messages name the file, the page (1-based) where relevant, and the setting that
was exceeded, so a person can act on them without reading code.
"""

MB = 1024 * 1024


class DocumentError(Exception):
    """A document could not be read. Catch this to handle every case below."""


class FileTooLargeError(DocumentError):
    def __init__(self, name: str, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            f"{name} is {size_bytes / MB:.1f} MB, over the {limit_bytes / MB:g} MB limit "
            "(ExtractionLimits.max_file_bytes); it was not opened"
        )


class TooManyPagesError(DocumentError):
    def __init__(self, name: str, pages: int, limit: int) -> None:
        super().__init__(
            f"{name} has {pages} pages, over the limit of {limit} (ExtractionLimits.max_pages)"
        )


class PageTimeoutError(DocumentError):
    def __init__(self, name: str, page_number: int | None, timeout_s: float, stage: str) -> None:
        where = f"{name} page {page_number}" if page_number is not None else name
        super().__init__(
            f"{where}: {stage} took longer than {timeout_s:g} s (ExtractionLimits.page_timeout_s)"
        )


class DocumentTimeoutError(DocumentError):
    def __init__(self, name: str, timeout_s: float) -> None:
        super().__init__(
            f"{name}: the whole document took longer than {timeout_s:g} s "
            "(ExtractionLimits.max_document_s)"
        )


class CorruptDocumentError(DocumentError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name} is not a readable PDF: {reason}")


class ContentMismatchError(DocumentError):
    """The file is not the document the caller expected: it was changed or damaged."""

    def __init__(self, name: str, expected_sha256: str, actual_sha256: str | None) -> None:
        found = f"sha256 {actual_sha256[:12]}..." if actual_sha256 else "no readable file"
        super().__init__(
            f"{name} is not the expected document (sha256 {expected_sha256[:12]}...): "
            f"found {found}; it was changed or damaged"
        )


class OcrUnavailableError(DocumentError):
    def __init__(self, problem: str) -> None:
        super().__init__(
            f"OCR is needed but unavailable: {problem}. Install Tesseract with English and "
            "Hindi: apt-get install -y tesseract-ocr tesseract-ocr-hin"
        )


class OcrFailedError(DocumentError):
    def __init__(self, name: str, page_number: int, detail: str) -> None:
        super().__init__(f"{name} page {page_number}: OCR failed: {detail}")


class WorkerStartError(DocumentError):
    """A worker process could not start: a problem with this machine, not the document."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"could not start a document worker process: {detail}")
