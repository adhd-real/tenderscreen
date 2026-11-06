"""SQLite implementation of ``StoreBackend`` (standard library ``sqlite3``).

Timestamps are stored as ISO 8601 text with their +05:30 offset and the SQL is
kept to what Postgres also accepts (``ON CONFLICT ... DO NOTHING/UPDATE``), so a
Postgres backend can reuse the statements with ``%s`` placeholders (and
``BYTEA`` instead of ``BLOB``). Every ``sqlite3`` error surfaces as
``StoreError``, so callers never depend on the database library.
"""

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Final

from tenderscreen.ist import to_ist
from tenderscreen.store.errors import StoreError
from tenderscreen.store.records import CacheEntry, CacheKey, DocumentRecord, Source

SCHEMA_VERSION: Final = 1
_SETUP_ATTEMPTS: Final = 20
_TABLES: Final = (
    """CREATE TABLE IF NOT EXISTS schema_info (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        version INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS documents (
        sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        content_type TEXT NOT NULL,
        first_saved_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS document_sources (
        sha256 TEXT NOT NULL REFERENCES documents (sha256),
        source_url TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (sha256, source_url, fetched_at)
    )""",
    """CREATE TABLE IF NOT EXISTS step_results (
        sha256 TEXT NOT NULL,
        step TEXT NOT NULL,
        version TEXT NOT NULL,
        payload BLOB NOT NULL,
        checksum TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (sha256, step, version)
    )""",
)


def _timestamp(moment: datetime) -> str:
    return to_ist(moment).isoformat()


def _from_timestamp(text: str) -> datetime:
    return to_ist(datetime.fromisoformat(text))


@contextmanager
def _errors(action: str) -> Iterator[None]:
    """Turn any ``sqlite3`` error into ``StoreError``."""
    try:
        yield
    except sqlite3.Error as exc:
        raise StoreError(f"store database error while {action}: {exc}") from exc


class SQLiteBackend:
    """Store metadata and step results in one SQLite file (or ``":memory:"``)."""

    def __init__(self, path: Path | str) -> None:
        try:
            self._conn = sqlite3.connect(path, timeout=30)
        except sqlite3.Error as exc:  # e.g. the path is a folder, or cannot be created
            raise StoreError(f"{path} cannot be opened as a store database: {exc}") from exc
        try:
            self._setup(on_disk=str(path) != ":memory:")
        except sqlite3.Error as exc:
            self._conn.close()
            raise StoreError(f"{path} is not a usable store database: {exc}") from exc
        except BaseException:  # e.g. an unknown schema version: still release the file
            self._conn.close()
            raise

    def _setup(self, *, on_disk: bool) -> None:
        """Create the tables once, safely even when several processes open a new store.

        A fresh database can be locked briefly by another process doing the same
        setup; that is retried rather than reported.
        """
        for attempt in range(_SETUP_ATTEMPTS):
            try:
                self._conn.execute("PRAGMA foreign_keys = ON")
                if on_disk:
                    mode = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
                    if mode != "wal":
                        self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _TABLES:
                        self._conn.execute(statement)
                    self._conn.execute(
                        "INSERT INTO schema_info (id, version) VALUES (1, ?) "
                        "ON CONFLICT (id) DO NOTHING",
                        (SCHEMA_VERSION,),
                    )
                    (version,) = self._conn.execute(
                        "SELECT version FROM schema_info WHERE id = 1"
                    ).fetchone()
                    self._conn.commit()
                except BaseException:
                    self._conn.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or attempt == _SETUP_ATTEMPTS - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
                continue
            if version != SCHEMA_VERSION:
                raise StoreError(
                    f"store database has schema version {version}, expected {SCHEMA_VERSION}"
                )
            return

    def record_document(
        self, sha256: str, size_bytes: int, content_type: str, saved_at: datetime, source: Source
    ) -> bool:
        with _errors("recording a document"), self._conn:
            self._conn.execute(
                "INSERT INTO documents (sha256, size_bytes, content_type, first_saved_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (sha256) DO NOTHING",
                (sha256, size_bytes, content_type, _timestamp(saved_at)),
            )
            cursor = self._conn.execute(
                "INSERT INTO document_sources (sha256, source_url, fetched_at) "
                "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (sha256, source.url, _timestamp(source.fetched_at)),
            )
        return cursor.rowcount == 1

    def get_document(self, sha256: str) -> DocumentRecord | None:
        with _errors("reading a document"):
            row = self._conn.execute(
                "SELECT size_bytes, content_type, first_saved_at FROM documents WHERE sha256 = ?",
                (sha256,),
            ).fetchone()
            if row is None:
                return None
            sources = self._conn.execute(
                "SELECT source_url, fetched_at FROM document_sources WHERE sha256 = ? "
                "ORDER BY fetched_at, source_url",
                (sha256,),
            ).fetchall()
        try:
            return DocumentRecord(
                sha256=sha256,
                size_bytes=row[0],
                content_type=row[1],
                first_saved_at=_from_timestamp(row[2]),
                sources=tuple(Source(url, _from_timestamp(fetched)) for url, fetched in sources),
            )
        except (ValueError, TypeError) as exc:  # a row this code would never have written
            raise StoreError(f"store database has a malformed record for {sha256}: {exc}") from exc

    def get_entry(self, key: CacheKey) -> CacheEntry | None:
        with _errors("reading a cached result"):
            row = self._conn.execute(
                "SELECT payload, checksum FROM step_results "
                "WHERE sha256 = ? AND step = ? AND version = ?",
                (key.sha256, key.step, key.version),
            ).fetchone()
        if row is None:
            return None
        payload, checksum = row
        if not isinstance(payload, bytes) or not isinstance(checksum, str):
            return CacheEntry(payload=b"", checksum="")  # wrong types: fails verification
        return CacheEntry(payload=payload, checksum=checksum)

    def put_entry(self, key: CacheKey, entry: CacheEntry, created_at: datetime) -> None:
        """Store a result. Other versions stay (they simply never match this key)."""
        with _errors("storing a cached result"), self._conn:
            self._conn.execute(
                "INSERT INTO step_results (sha256, step, version, payload, checksum, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (sha256, step, version) DO UPDATE SET "
                "payload = excluded.payload, checksum = excluded.checksum, "
                "created_at = excluded.created_at",
                (
                    key.sha256,
                    key.step,
                    key.version,
                    entry.payload,
                    entry.checksum,
                    _timestamp(created_at),
                ),
            )

    def delete_entry(self, key: CacheKey) -> None:
        with _errors("deleting a cached result"), self._conn:
            self._conn.execute(
                "DELETE FROM step_results WHERE sha256 = ? AND step = ? AND version = ?",
                (key.sha256, key.step, key.version),
            )

    def delete_step_versions(self, step: str, keep_version: str) -> int:
        with _errors("deleting old cached results"), self._conn:
            cursor = self._conn.execute(
                "DELETE FROM step_results WHERE step = ? AND version <> ?", (step, keep_version)
            )
        return cursor.rowcount

    def close(self) -> None:
        self._conn.close()
