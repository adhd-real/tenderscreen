"""Raw documents deduplicated by sha256, and a cache of every step's results.

with open_store() as store:                   # data/raw/ and data/raw/store.sqlite3
    doc = store.documents.save_document(pdf_bytes, url, fetched_at)
    pages = extract_pages(doc.path, cache=store.cache, expected_sha256=doc.sha256)
"""

from tenderscreen.store.backend import StoreBackend
from tenderscreen.store.cache import CacheStats, ResultCache
from tenderscreen.store.documents import DocumentStore, StoredDocument, sha256_file
from tenderscreen.store.errors import (
    CorruptStoredFileError,
    DocumentTooLargeError,
    StoreError,
    UnknownDocumentError,
)
from tenderscreen.store.facade import DEFAULT_ROOT, Store, open_store, save_document
from tenderscreen.store.records import CacheKey, DocumentRecord, Source
from tenderscreen.store.sqlite import SQLiteBackend

__all__ = [
    "DEFAULT_ROOT",
    "CacheKey",
    "CacheStats",
    "CorruptStoredFileError",
    "DocumentRecord",
    "DocumentStore",
    "DocumentTooLargeError",
    "ResultCache",
    "SQLiteBackend",
    "Source",
    "Store",
    "StoreBackend",
    "StoreError",
    "StoredDocument",
    "UnknownDocumentError",
    "open_store",
    "save_document",
    "sha256_file",
]
