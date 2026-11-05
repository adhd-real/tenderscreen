"""SQLite implementation of ``StoreBackend`` (standard library ``sqlite3``).

Timestamps are stored as ISO 8601 text with their +05:30 offset and the SQL is
kept to what Postgres also accepts (``ON CONFLICT ... DO NOTHING/UPDATE``), so a
Postgres backend can reuse the same statements with ``%s`` placeholders.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Final

from tenderscreen.ist import to_ist
from tenderscreen.store.errors import StoreError
from tenderscreen.store.records import CacheEntry, CacheKey, DocumentRecord, Source

SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    content_type TEXT NOT NULL,
    first_saved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_sources (
    sha256 TEXT NOT NULL REFERENCES documents (sha256),
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (sha256, source_url, fetched_at)
);
CREATE TABLE IF NOT EXISTS step_results (
    sha256 TEXT NOT NULL,
    step TEXT NOT NULL,
    version TEXT NOT NULL,
    payload BLOB NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (sha256, step, version)
);
"""


def _timestamp(moment: datetime) -> str:
    return to_ist(moment).isoformat()


def _from_timestamp(text: str) -> datetime:
    return to_ist(datetime.fromisoformat(text))


class SQLiteBackend:
    """Store metadata and step results in one SQLite file (or ``":memory:"``)."""

    def __init__(self, path: Path | str) -> None:
        self._conn = sqlite3.connect(path, timeout=30)
        try:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if str(path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(_SCHEMA)
            self._check_schema_version()
        except sqlite3.DatabaseError as exc:
            self._conn.close()
            raise StoreError(f"{path} is not a usable store database: {exc}") from exc
        except BaseException:  # e.g. an unknown schema version: still release the file
            self._conn.close()
            raise

    def _check_schema_version(self) -> None:
        rows = self._conn.execute("SELECT version FROM schema_info").fetchall()
        if not rows:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO schema_info (version) VALUES (?)", (SCHEMA_VERSION,)
                )
        elif rows != [(SCHEMA_VERSION,)]:
            raise StoreError(f"store database has schema version {rows}, expected {SCHEMA_VERSION}")

    def record_document(
        self, sha256: str, size_bytes: int, content_type: str, saved_at: datetime
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO documents (sha256, size_bytes, content_type, first_saved_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (sha256) DO NOTHING",
                (sha256, size_bytes, content_type, _timestamp(saved_at)),
            )

    def add_source(self, sha256: str, source: Source) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO document_sources (sha256, source_url, fetched_at) "
                "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (sha256, source.url, _timestamp(source.fetched_at)),
            )
        return cursor.rowcount == 1

    def get_document(self, sha256: str) -> DocumentRecord | None:
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
        return DocumentRecord(
            sha256=sha256,
            size_bytes=row[0],
            content_type=row[1],
            first_saved_at=_from_timestamp(row[2]),
            sources=tuple(Source(url, _from_timestamp(fetched)) for url, fetched in sources),
        )

    def get_entry(self, key: CacheKey) -> CacheEntry | None:
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
        with self._conn:
            self._conn.execute(
                "DELETE FROM step_results WHERE sha256 = ? AND step = ? AND version <> ?",
                (key.sha256, key.step, key.version),
            )
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
        with self._conn:
            self._conn.execute(
                "DELETE FROM step_results WHERE sha256 = ? AND step = ? AND version = ?",
                (key.sha256, key.step, key.version),
            )

    def delete_step_versions(self, step: str, keep_version: str) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM step_results WHERE step = ? AND version <> ?", (step, keep_version)
            )
        return cursor.rowcount

    def close(self) -> None:
        self._conn.close()
