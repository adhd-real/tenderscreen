"""Cache of step results keyed by (document sha256, step name, step version).

No step ever redoes work for the same document and version. Bumping a step's
version makes old entries miss, and storing the new result deletes them. Every
entry is checked before use: if its bytes no longer match the checksum written
with them, or they cannot be decoded, the entry is deleted and the step runs
again. A cached value is never trusted blindly.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from tenderscreen.ist import now_ist
from tenderscreen.store.backend import StoreBackend
from tenderscreen.store.records import CacheEntry, CacheKey


@dataclass(slots=True)
class CacheStats:
    hits: int = 0
    misses: int = 0
    discarded: int = 0  # entries found corrupt and recomputed


def checksum(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class ResultCache:
    def __init__(self, backend: StoreBackend) -> None:
        self._backend = backend
        self.stats = CacheStats()

    def get(self, key: CacheKey) -> bytes | None:
        """Verified payload for ``key``; a corrupt entry is deleted and reported as a miss."""
        entry = self._backend.get_entry(key)
        if entry is None:
            return None
        if checksum(entry.payload) != entry.checksum:
            self._discard(key)
            return None
        return entry.payload

    def put(self, key: CacheKey, payload: bytes) -> None:
        """Store ``payload``; results of other versions of this step for this document go."""
        self._backend.put_entry(key, CacheEntry(payload, checksum(payload)), now_ist())

    def get_or_compute[T](
        self,
        key: CacheKey,
        compute: Callable[[], T],
        *,
        encode: Callable[[T], bytes],
        decode: Callable[[bytes], T],
    ) -> T:
        """Cached value for ``key``, else ``compute()`` it and cache it.

        ``decode`` must raise ``ValueError`` for anything it cannot fully validate;
        such an entry is discarded and recomputed.
        """
        payload = self.get(key)
        if payload is not None:
            try:
                value = decode(payload)
            except ValueError:
                self._discard(key)
            else:
                self.stats.hits += 1
                return value
        self.stats.misses += 1
        value = compute()
        self.put(key, encode(value))
        return value

    def invalidate_step(self, step: str, keep_version: str) -> int:
        """Delete every result of ``step`` except those of ``keep_version``."""
        return self._backend.delete_step_versions(step, keep_version)

    def _discard(self, key: CacheKey) -> None:
        self._backend.delete_entry(key)
        self.stats.discarded += 1
