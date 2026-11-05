import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

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


def test_record_and_get_document(backend: SQLiteBackend) -> None:
    backend.record_document(SHA, 123, "application/pdf", WHEN)
    record = backend.get_document(SHA)
    assert record is not None
    assert (record.size_bytes, record.content_type, record.sources) == (123, "application/pdf", ())
    assert record.first_saved_at == WHEN
    assert record.first_saved_at.tzinfo is IST


def test_record_document_keeps_the_first_row(backend: SQLiteBackend) -> None:
    backend.record_document(SHA, 123, "application/pdf", WHEN)
    backend.record_document(SHA, 999, "text/plain", datetime(2030, 1, 1, tzinfo=IST))
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
    backend.record_document(SHA, 1, "text/plain", WHEN)
    later = Source("https://b.example/x", datetime(2026, 10, 16, tzinfo=UTC))
    earlier = Source("https://a.example/x", datetime(2026, 10, 15, tzinfo=UTC))
    assert backend.add_source(SHA, later) is True
    assert backend.add_source(SHA, earlier) is True
    assert backend.add_source(SHA, later) is False  # exact duplicate
    record = backend.get_document(SHA)
    assert record is not None
    assert record.sources == (earlier, later)


def test_source_needs_an_existing_document(backend: SQLiteBackend) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        backend.add_source(SHA, Source("https://a.example/x", WHEN))


def test_put_get_and_delete_entry(backend: SQLiteBackend) -> None:
    key = CacheKey(SHA, "step", "1")
    assert backend.get_entry(key) is None
    backend.put_entry(key, CacheEntry(b"payload", "sum"), WHEN)
    assert backend.get_entry(key) == CacheEntry(b"payload", "sum")
    backend.put_entry(key, CacheEntry(b"newer", "sum2"), WHEN)  # same key: replaced
    assert backend.get_entry(key) == CacheEntry(b"newer", "sum2")
    backend.delete_entry(key)
    assert backend.get_entry(key) is None


def test_put_entry_removes_other_versions_of_the_same_step_and_document(
    backend: SQLiteBackend,
) -> None:
    old = CacheKey(SHA, "step", "1")
    other_doc = CacheKey(OTHER, "step", "1")
    other_step = CacheKey(SHA, "other", "1")
    for key in (old, other_doc, other_step):
        backend.put_entry(key, CacheEntry(b"x", "s"), WHEN)
    backend.put_entry(CacheKey(SHA, "step", "2"), CacheEntry(b"y", "t"), WHEN)
    assert backend.get_entry(old) is None
    assert backend.get_entry(other_doc) is not None
    assert backend.get_entry(other_step) is not None


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
    first.record_document(SHA, 5, "text/plain", WHEN)
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
    run_sql(db, "UPDATE schema_info SET version = ?", (SCHEMA_VERSION + 1,))
    with pytest.raises(StoreError, match="schema version"):
        SQLiteBackend(db)
