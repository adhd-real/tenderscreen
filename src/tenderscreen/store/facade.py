"""One-call access to the default store under ``data/raw/``."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from tenderscreen.store.backend import StoreBackend
from tenderscreen.store.cache import ResultCache
from tenderscreen.store.documents import DocumentStore, StoredDocument
from tenderscreen.store.sqlite import SQLiteBackend

# Git-ignored scratch area (see .gitignore and CLAUDE.md layout).
DEFAULT_ROOT: Final = Path("data/raw")
DATABASE_NAME: Final = "store.sqlite3"


@dataclass(frozen=True, slots=True)
class Store:
    """Documents and the result cache, sharing one backend. Use as a context manager."""

    documents: DocumentStore
    cache: ResultCache
    backend: StoreBackend

    def close(self) -> None:
        self.backend.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def open_store(root: Path = DEFAULT_ROOT) -> Store:
    """Open (creating if needed) the SQLite-backed store in ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    backend = SQLiteBackend(root / DATABASE_NAME)
    return Store(DocumentStore(root, backend), ResultCache(backend), backend)


def save_document(
    content: bytes, source_url: str, fetched_at: datetime, *, root: Path = DEFAULT_ROOT
) -> StoredDocument:
    """Store raw bytes under ``root`` named by their sha256, with source and IST fetch time."""
    with open_store(root) as store:
        return store.documents.save_document(content, source_url, fetched_at)
