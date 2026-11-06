"""Plain records passed between the store and its storage backend."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from tenderscreen.ist import to_ist

_SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")
MAX_URL_CHARS: Final = 2048
SOURCE_SCHEMES: Final = frozenset({"http", "https", "file"})


def check_sha256(value: str) -> str:
    """Return ``value`` if it is a lowercase hex sha256 digest, else raise ValueError."""
    if not _SHA256_HEX.fullmatch(value):
        raise ValueError(f"not a lowercase hex sha256 digest: {value[:80]!r}")
    return value


@dataclass(frozen=True, slots=True)
class Source:
    """Where and when a document's bytes were fetched."""

    url: str
    fetched_at: datetime  # timezone-aware, Asia/Kolkata

    def __post_init__(self) -> None:
        check_source_url(self.url)
        # Refuses naive datetimes and normalises any offset to IST.
        object.__setattr__(self, "fetched_at", to_ist(self.fetched_at))


def check_source_url(url: str) -> None:
    """Accept http, https and file URLs only, with no spaces or invisible characters.

    file: covers documents uploaded by hand; anything else (javascript:, data:)
    has no business being a document's source. Lone surrogates (an undecodable
    file name) are refused too: they cannot be stored as text.
    """
    if not url or len(url) > MAX_URL_CHARS:
        raise ValueError(f"source URL must be 1 to {MAX_URL_CHARS} characters")
    if any(ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf", "Cs") for ch in url):
        raise ValueError(f"source URL has spaces or control characters: {url[:80]!r}")
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    if scheme not in SOURCE_SCHEMES:
        raise ValueError(f"source URL must start with http:, https: or file:, not {url[:80]!r}")


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """Metadata of one stored document (one row per distinct content)."""

    sha256: str
    size_bytes: int
    content_type: str
    first_saved_at: datetime  # timezone-aware, Asia/Kolkata
    sources: tuple[Source, ...]


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Identifies one step's result for one document."""

    sha256: str
    step: str
    version: str

    def __post_init__(self) -> None:
        check_sha256(self.sha256)
        if not self.step or not self.version:
            raise ValueError("cache key needs a step name and a step version")


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """A cached result as stored: the bytes and the sha256 they had when written."""

    payload: bytes
    checksum: str
