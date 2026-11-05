from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tenderscreen.ist import IST
from tenderscreen.store.documents import (
    DocumentStore,
    sha256_bytes,
    sha256_file,
    sniff_content_type,
)
from tenderscreen.store.errors import (
    CorruptStoredFileError,
    DocumentTooLargeError,
    UnknownDocumentError,
)
from tenderscreen.store.sqlite import SQLiteBackend

PDF = b"%PDF-1.7\nsynthetic test document\n%%EOF\n"
URL_A = "https://eprocure.gov.in/synthetic/a.pdf"
URL_B = "https://eproc.punjab.gov.in/synthetic/b.pdf"
WHEN = datetime(2026, 10, 15, 11, 30, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[DocumentStore]:
    backend = SQLiteBackend(":memory:")
    yield DocumentStore(tmp_path / "raw", backend)
    backend.close()


def stored_files(store: DocumentStore) -> list[str]:
    return sorted(p.name for p in store.root.iterdir())


def test_save_writes_bytes_named_by_sha256(store: DocumentStore) -> None:
    doc = store.save_document(PDF, URL_A, WHEN)
    assert doc.sha256 == sha256_bytes(PDF)
    assert doc.path == store.root / doc.sha256
    assert doc.path.read_bytes() == PDF
    assert (doc.size_bytes, doc.content_type) == (len(PDF), "application/pdf")
    assert doc.first_saved_at.tzinfo is IST


def test_fetch_time_is_stored_in_ist(store: DocumentStore) -> None:
    (source,) = store.save_document(PDF, URL_A, WHEN).sources
    assert source.url == URL_A
    assert source.fetched_at.tzinfo is IST
    assert source.fetched_at == WHEN
    assert (source.fetched_at.hour, source.fetched_at.minute) == (17, 0)


def test_identical_bytes_are_stored_once_with_every_source(store: DocumentStore) -> None:
    first = store.save_document(PDF, URL_A, WHEN)
    before = first.path.stat()
    second = store.save_document(PDF, URL_B, datetime(2026, 10, 16, tzinfo=IST))
    after = second.path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)  # not rewritten
    assert first.sha256 == second.sha256
    assert stored_files(store) == [first.sha256]
    assert [s.url for s in second.sources] == [URL_A, URL_B]
    assert second.first_saved_at == first.first_saved_at


def test_same_source_twice_is_recorded_once(store: DocumentStore) -> None:
    store.save_document(PDF, URL_A, WHEN)
    doc = store.save_document(PDF, URL_A, WHEN)
    assert len(doc.sources) == 1


def test_different_bytes_get_different_files(store: DocumentStore) -> None:
    a = store.save_document(PDF, URL_A, WHEN)
    b = store.save_document(PDF + b" ", URL_A, WHEN)
    assert a.sha256 != b.sha256
    assert stored_files(store) == sorted([a.sha256, b.sha256])


def test_damaged_file_is_rewritten_on_save(store: DocumentStore) -> None:
    doc = store.save_document(PDF, URL_A, WHEN)
    doc.path.write_bytes(b"damaged")
    with pytest.raises(CorruptStoredFileError, match="no longer matches"):
        store.read_bytes(doc.sha256)
    store.save_document(PDF, URL_B, WHEN)
    assert store.read_bytes(doc.sha256) == PDF


def test_read_unknown_document(store: DocumentStore) -> None:
    with pytest.raises(UnknownDocumentError):
        store.read_bytes("d" * 64)
    with pytest.raises(UnknownDocumentError):
        store.require("d" * 64)
    assert store.get("d" * 64) is None


def test_oversized_document_is_refused(tmp_path: Path) -> None:
    backend = SQLiteBackend(":memory:")
    store = DocumentStore(tmp_path, backend, max_bytes=10)
    with pytest.raises(DocumentTooLargeError, match="over the store limit of 10 bytes"):
        store.save_document(PDF, URL_A, WHEN)
    backend.close()
    assert list(tmp_path.iterdir()) == []


def test_naive_fetch_time_is_refused_before_writing(store: DocumentStore) -> None:
    with pytest.raises(ValueError, match="naive"):
        store.save_document(PDF, URL_A, datetime(2026, 10, 15))  # noqa: DTZ001 - testing the guard
    assert stored_files(store) == []


def test_path_for_refuses_non_digests(store: DocumentStore) -> None:
    with pytest.raises(ValueError, match="sha256"):
        store.path_for("../../etc/passwd")


def test_failed_write_leaves_no_temporary_file(
    store: DocumentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(src: str, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("tenderscreen.store.documents.os.replace", fail)
    with pytest.raises(OSError, match="disk full"):
        store.save_document(PDF, URL_A, WHEN)
    assert stored_files(store) == []


def test_sha256_file_matches_bytes(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    content = b"x" * (3 * 1024 * 1024 + 7)  # spans several read chunks
    path.write_bytes(content)
    assert sha256_file(path) == sha256_bytes(content)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (PDF, "application/pdf"),
        (b"\x00" * 10 + b"%PDF-1.4", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n...", "image/png"),
        (b"\xff\xd8\xff\xe0...", "image/jpeg"),
        (b"GIF89a...", "image/gif"),
        (b"GIF87a...", "image/gif"),
        (b"II*\x00...", "image/tiff"),
        (b"MM\x00*...", "image/tiff"),
        (b"PK\x03\x04...", "application/zip"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1...", "application/x-ole-storage"),
        (b"\xef\xbb\xbf  <!DOCTYPE html><html>", "text/html"),
        (b"<HTML><body>notice</body>", "text/html"),
        (b"<?xml version='1.0'?><a/>", "application/xml"),
        (b"<!-- saved from url=(0040)https://eprocure.gov.in/ -->\n<html>", "text/html"),
        (b"<head><title>AOC</title></head>", "text/html"),
        (b"<?xml version='1.0'?>\n<!DOCTYPE html><html xmlns='x'>", "text/html"),
        ("निविदा सूचना".encode(), "text/plain"),
        (b"", "text/plain"),
        (b"text with \x00 nul", "application/octet-stream"),
        (b"\xff\xfe\xfa binary", "application/octet-stream"),
    ],
)
def test_sniff_content_type(content: bytes, expected: str) -> None:
    assert sniff_content_type(content) == expected


def test_directory_that_cannot_be_opened_is_not_fatal(tmp_path: Path) -> None:
    from tenderscreen.store import documents

    documents._sync_directory(tmp_path / "no-such-folder")  # returns quietly


def test_directory_fsync_refusal_is_not_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tenderscreen.store import documents

    def cannot_sync(fd: int) -> None:
        raise OSError("fsync not supported on directories here")

    monkeypatch.setattr("tenderscreen.store.documents.os.fsync", cannot_sync)
    documents._sync_directory(tmp_path)
