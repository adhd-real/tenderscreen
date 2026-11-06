"""Errors raised by the document store. All derive from ``StoreError``."""


class StoreError(Exception):
    """The store could not complete an operation."""


class DocumentTooLargeError(StoreError):
    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            f"document is {size_bytes} bytes, over the store limit of {limit_bytes} bytes"
        )


class UnknownDocumentError(StoreError):
    def __init__(self, sha256: str) -> None:
        super().__init__(f"no stored document with sha256 {sha256}")


class CorruptStoredFileError(StoreError):
    def __init__(self, sha256: str) -> None:
        super().__init__(
            f"stored file {sha256} no longer matches its sha256; save the document again"
        )
