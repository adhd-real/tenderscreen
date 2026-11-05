"""The storage interface. SQLite implements it today; Postgres can later.

Everything the store needs from a database goes through these methods, using
plain SQL that both SQLite and Postgres accept, so switching means writing one
new class, not touching callers. Implementations raise ``StoreError`` for
database failures.
"""

from datetime import datetime
from typing import Protocol

from tenderscreen.store.records import CacheEntry, CacheKey, DocumentRecord, Source


class StoreBackend(Protocol):
    def record_document(
        self, sha256: str, size_bytes: int, content_type: str, saved_at: datetime, source: Source
    ) -> bool:
        """In one transaction: add the document row if new (the first one is kept) and
        attach ``source``. False if that exact source was already recorded."""

    def get_document(self, sha256: str) -> DocumentRecord | None:
        """The document and all its sources, or None."""

    def get_entry(self, key: CacheKey) -> CacheEntry | None:
        """The cached result for exactly this key, or None."""

    def put_entry(self, key: CacheKey, entry: CacheEntry, created_at: datetime) -> None:
        """Store a result for exactly this key (other versions are kept)."""

    def delete_entry(self, key: CacheKey) -> None:
        """Remove one cached result (for example because it failed its checksum)."""

    def delete_step_versions(self, step: str, keep_version: str) -> int:
        """Remove every result of ``step`` whose version is not ``keep_version``."""

    def close(self) -> None:
        """Release the connection."""
