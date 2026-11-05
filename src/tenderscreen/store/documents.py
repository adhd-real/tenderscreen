"""Raw document bytes on disk, named by their sha256, with metadata in the backend.

Saving the same bytes again (from another URL, or the same URL later) keeps one
file and adds the new source to its metadata: a document is never stored or
processed twice (CLAUDE.md performance rules).
"""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Final

from tenderscreen.ist import now_ist
from tenderscreen.store.backend import StoreBackend
from tenderscreen.store.errors import (
    CorruptStoredFileError,
    DocumentTooLargeError,
    UnknownDocumentError,
)
from tenderscreen.store.records import Source, check_sha256

DEFAULT_MAX_BYTES: Final = 100 * 1024 * 1024
_CHUNK: Final = 1024 * 1024
_SNIFF_BYTES: Final = 1024


@dataclass(frozen=True, slots=True)
class StoredDocument:
    sha256: str
    path: Path
    size_bytes: int
    content_type: str
    first_saved_at: datetime  # timezone-aware, Asia/Kolkata
    sources: tuple[Source, ...]


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(_CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    """sha256 of a file, read in chunks so large files do not fill memory."""
    with path.open("rb") as stream:
        return sha256_stream(stream)


def sniff_content_type(content: bytes) -> str:
    """Media type from the bytes themselves (servers' Content-Type headers lie)."""
    head = content[:_SNIFF_BYTES]
    signatures = (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"II*\x00", "image/tiff"),
        (b"MM\x00*", "image/tiff"),
        (b"PK\x03\x04", "application/zip"),  # also .docx, .xlsx, .odt
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole-storage"),  # .doc, .xls
    )
    for magic, media_type in signatures:
        if head.startswith(magic):
            return media_type
    if b"%PDF-" in head:
        return "application/pdf"
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    # Saved web pages often start with a comment ("<!-- saved from url=...") or
    # an XML prolog (XHTML), so look for HTML markers anywhere in the first 1 KB.
    if text.startswith(b"<") and any(
        marker in text for marker in (b"<!doctype html", b"<html", b"<head", b"<body")
    ):
        return "text/html"
    if text.startswith(b"<?xml"):
        return "application/xml"
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    return "application/octet-stream" if b"\x00" in content else "text/plain"


class DocumentStore:
    """Raw documents under ``root`` (``data/raw/`` by default), one file per content."""

    def __init__(
        self, root: Path, backend: StoreBackend, *, max_bytes: int = DEFAULT_MAX_BYTES
    ) -> None:
        self.root = root
        self._backend = backend
        self._max_bytes = max_bytes
        root.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha256: str) -> Path:
        return self.root / check_sha256(sha256)

    def save_document(
        self, content: bytes, source_url: str, fetched_at: datetime
    ) -> StoredDocument:
        """Store ``content`` once and record where and when it was fetched (in IST)."""
        if len(content) > self._max_bytes:
            raise DocumentTooLargeError(len(content), self._max_bytes)
        source = Source(source_url, fetched_at)  # validates the URL; refuses naive times
        sha256 = sha256_bytes(content)
        self._write_once(sha256, content)
        content_type = sniff_content_type(content)
        self._backend.record_document(sha256, len(content), content_type, now_ist(), source)
        return self.require(sha256)

    def get(self, sha256: str) -> StoredDocument | None:
        """Metadata of a stored document, or None if unknown."""
        record = self._backend.get_document(check_sha256(sha256))
        if record is None:
            return None
        return StoredDocument(
            sha256=record.sha256,
            path=self.path_for(record.sha256),
            size_bytes=record.size_bytes,
            content_type=record.content_type,
            first_saved_at=record.first_saved_at,
            sources=record.sources,
        )

    def require(self, sha256: str) -> StoredDocument:
        """Metadata of a stored document; ``UnknownDocumentError`` if there is none."""
        stored = self.get(sha256)
        if stored is None:
            raise UnknownDocumentError(sha256)
        return stored

    def read_bytes(self, sha256: str) -> bytes:
        """The stored bytes, checked against their sha256 (never trusted blindly)."""
        path = self.path_for(sha256)
        try:
            content = path.read_bytes()
        except FileNotFoundError as exc:
            raise UnknownDocumentError(sha256) from exc
        if sha256_bytes(content) != sha256:
            raise CorruptStoredFileError(sha256)
        return content

    def _write_once(self, sha256: str, content: bytes) -> None:
        """Write the file unless an intact copy exists; replace a damaged copy atomically."""
        path = self.path_for(sha256)
        if path.is_file() and sha256_file(path) == sha256:
            return
        fd, temp_name = tempfile.mkstemp(dir=self.root, prefix=".incoming-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
        sync_directory(self.root)


def sync_directory(directory: Path) -> None:
    """Make a rename in ``directory`` durable (POSIX; a no-op where unsupported)."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # some filesystems refuse fsync on a directory; the data itself is synced
    finally:
        os.close(fd)
