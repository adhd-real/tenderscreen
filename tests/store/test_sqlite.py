import sqlite3
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from tenderscreen.ist import IST
from tenderscreen.store.errors import StoreError
from tenderscreen.store.records import CacheEntry, CacheKey, Source
from tenderscreen.store.sqlite import SCHEMA_VERSION, SQLiteBackend
from tests.sqlite_tools import run_sql

SHA = "b" * 64
OTHER = "c" * 64
WHEN = datetime(2026, 10, 15, 17, 0, tzinfo=IST)


@pytest.fixture
def backend() -> Iterator[SQLiteBackend]:
    backend = SQLiteBackend(":memory:")
    yield backend
    backend.close()


SOURCE = Source("https://eprocure.gov.in/synthetic/x.pdf", WHEN)


def test_record_and_get_document(backend: SQLiteBackend) -> None:
    assert backend.record_document(SHA, 123, "application/pdf", WHEN, SOURCE) is True
    record = backend.get_document(SHA)
    assert record is not None
    assert (record.size_bytes, record.content_type, record.sources) == (
        123,
        "application/pdf",
        (SOURCE,),
    )
    assert record.first_saved_at == WHEN
    assert record.first_saved_at.tzinfo is IST


def test_record_document_keeps_the_first_row(backend: SQLiteBackend) -> None:
    backend.record_document(SHA, 123, "application/pdf", WHEN, SOURCE)
    backend.record_document(SHA, 999, "text/plain", datetime(2030, 1, 1, tzinfo=IST), SOURCE)
    record = backend.get_document(SHA)
    assert record is not None
    assert (record.size_bytes, record.content_type, record.first_saved_at) == (
        123,
        "application/pdf",
        WHEN,
    )


def test_unknown_document(backend: SQLiteBackend) -> None:
    assert backend.get_document(SHA) is None


def test_sources_are_added_once_and_ordered_by_time(backend: SQLiteBackend) -> None:
    later = Source("https://b.example/x", datetime(2026, 10, 16, tzinfo=UTC))
    earlier = Source("https://a.example/x", datetime(2026, 10, 15, tzinfo=UTC))
    assert backend.record_document(SHA, 1, "text/plain", WHEN, later) is True
    assert backend.record_document(SHA, 1, "text/plain", WHEN, earlier) is True
    assert backend.record_document(SHA, 1, "text/plain", WHEN, later) is False  # duplicate
    record = backend.get_document(SHA)
    assert record is not None
    assert record.sources == (earlier, later)


def test_database_errors_surface_as_store_errors(backend: SQLiteBackend) -> None:
    with pytest.raises(StoreError, match="while recording a document"):
        backend.record_document("short", 1, "text/plain", WHEN, SOURCE)  # breaks a CHECK
    backend.close()
    with pytest.raises(StoreError, match="while reading a document"):
        backend.get_document(SHA)


def test_put_get_and_delete_entry(backend: SQLiteBackend) -> None:
    key = CacheKey(SHA, "step", "1")
    assert backend.get_entry(key) is None
    backend.put_entry(key, CacheEntry(b"payload", "sum"), WHEN)
    assert backend.get_entry(key) == CacheEntry(b"payload", "sum")
    backend.put_entry(key, CacheEntry(b"newer", "sum2"), WHEN)  # same key: replaced
    assert backend.get_entry(key) == CacheEntry(b"newer", "sum2")
    backend.delete_entry(key)
    assert backend.get_entry(key) is None


def test_put_entry_keeps_other_versions(backend: SQLiteBackend) -> None:
    """Found in review: deleting other versions made callers with different
    settings evict each other's results on every call."""
    v1, v2 = CacheKey(SHA, "step", "1"), CacheKey(SHA, "step", "2")
    backend.put_entry(v1, CacheEntry(b"x", "s"), WHEN)
    backend.put_entry(v2, CacheEntry(b"y", "t"), WHEN)
    assert backend.get_entry(v1) == CacheEntry(b"x", "s")
    assert backend.get_entry(v2) == CacheEntry(b"y", "t")


def test_delete_step_versions(backend: SQLiteBackend) -> None:
    backend.put_entry(CacheKey(SHA, "step", "1"), CacheEntry(b"x", "s"), WHEN)
    backend.put_entry(CacheKey(OTHER, "step", "1"), CacheEntry(b"x", "s"), WHEN)
    backend.put_entry(CacheKey(OTHER, "keep", "1"), CacheEntry(b"x", "s"), WHEN)
    assert backend.delete_step_versions("step", keep_version="2") == 2
    assert backend.get_entry(CacheKey(OTHER, "keep", "1")) is not None


def test_entry_with_wrong_column_types_fails_verification(tmp_path: Path) -> None:
    db = tmp_path / "store.sqlite3"
    backend = SQLiteBackend(db)
    key = CacheKey(SHA, "step", "1")
    backend.put_entry(key, CacheEntry(b"x", "s"), WHEN)
    backend.close()
    run_sql(db, "UPDATE step_results SET payload = 'text, not bytes'")
    backend = SQLiteBackend(db)
    assert backend.get_entry(key) == CacheEntry(b"", "")
    backend.close()


def test_data_survives_reopening(tmp_path: Path) -> None:
    db = tmp_path / "store.sqlite3"
    first = SQLiteBackend(db)
    first.record_document(SHA, 5, "text/plain", WHEN, SOURCE)
    first.close()
    second = SQLiteBackend(db)
    assert second.get_document(SHA) is not None
    second.close()


def test_not_a_database_is_a_clear_error(tmp_path: Path) -> None:
    db = tmp_path / "store.sqlite3"
    db.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(StoreError, match="not a usable store database"):
        SQLiteBackend(db)


def test_unknown_schema_version_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "store.sqlite3"
    SQLiteBackend(db).close()
    run_sql(db, "UPDATE schema_info SET version = ? WHERE id = 1", (SCHEMA_VERSION + 1,))
    with pytest.raises(StoreError, match="schema version"):
        SQLiteBackend(db)


def test_many_processes_opening_a_new_store_at_once(tmp_path: Path) -> None:
    """Found in review: concurrent first opens could write two schema rows,
    after which every open failed."""
    for round_number in range(5):
        db = tmp_path / f"store-{round_number}.sqlite3"
        barrier = threading.Barrier(8)

        def open_close(db: Path = db, barrier: threading.Barrier = barrier) -> None:
            barrier.wait()
            SQLiteBackend(db).close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            for future in [pool.submit(open_close) for _ in range(8)]:
                future.result()
        assert run_sql(db, "SELECT id, version FROM schema_info") == [(1, SCHEMA_VERSION)]
        SQLiteBackend(db).close()


class LockedConnection:
    """Stands in for a connection whose database stays locked ``locked_for`` times."""

    def __init__(self, locked_for: int, message: str = "database is locked") -> None:
        self.locked_for = locked_for
        self.message = message
        self.commits = 0

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> "LockedConnection":
        if sql == "BEGIN IMMEDIATE" and self.locked_for > 0:
            self.locked_for -= 1
            raise sqlite3.OperationalError(self.message)
        return self

    def fetchone(self) -> tuple[object, ...]:
        return (SCHEMA_VERSION,)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


def bare_backend(conn: LockedConnection) -> SQLiteBackend:
    backend = SQLiteBackend.__new__(SQLiteBackend)
    backend._conn = cast(sqlite3.Connection, conn)
    return backend


def test_setup_retries_while_the_database_is_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tenderscreen.store.sqlite.time.sleep", lambda seconds: None)
    conn = LockedConnection(locked_for=3)
    bare_backend(conn)._setup(on_disk=False)
    assert conn.commits == 1


def test_setup_gives_up_after_many_locked_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tenderscreen.store.sqlite.time.sleep", lambda seconds: None)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        bare_backend(LockedConnection(locked_for=1000))._setup(on_disk=False)


def test_setup_does_not_retry_other_errors() -> None:
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        bare_backend(LockedConnection(locked_for=1, message="disk I/O error"))._setup(on_disk=False)


def test_wal_mode_is_set_once(tmp_path: Path) -> None:
    db = tmp_path / "store.sqlite3"
    SQLiteBackend(db).close()
    assert run_sql(db, "PRAGMA journal_mode") == [("wal",)]
    SQLiteBackend(db).close()  # already WAL: not switched again


class FailsInsideTransaction(LockedConnection):
    """Begins fine, then fails while creating the tables."""

    def __init__(self) -> None:
        super().__init__(locked_for=0)
        self.rolled_back = False

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> "LockedConnection":
        if sql.lstrip().startswith("CREATE TABLE"):
            raise sqlite3.OperationalError("disk I/O error")
        return super().execute(sql, params)

    def rollback(self) -> None:
        self.rolled_back = True


def test_setup_failure_inside_the_transaction_rolls_back() -> None:
    conn = FailsInsideTransaction()
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        bare_backend(conn)._setup(on_disk=False)
    assert conn.rolled_back
    assert conn.commits == 0
