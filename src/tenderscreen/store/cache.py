"""Cache of step results keyed by (document sha256, step name, step version).

No step ever redoes work for the same document and version. Changing a step's
version invalidates its entries: they no longer match any lookup (and
``invalidate_step`` deletes them). Every entry is checked before use: its
checksum covers the key and the bytes, so damage in place and an entry moved
to another key are both caught, and the bytes must decode; otherwise the entry
is deleted and the step runs again. A cached value is never trusted blindly.

The checksum has no secret key, so it is not a defence against someone who can
write to the database file: they could compute a valid checksum.
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


def checksum(key: CacheKey, payload: bytes) -> str:
    """sha256 over the key and the payload, so an entry is only valid for its own key."""
    digest = hashlib.sha256()
    for part in (key.sha256, key.step, key.version):
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big") + encoded)
    digest.update(payload)
    return digest.hexdigest()


class ResultCache:
    def __init__(self, backend: StoreBackend) -> None:
        self._backend = backend
        self.stats = CacheStats()

    def get(self, key: CacheKey) -> bytes | None:
        """Verified payload for ``key``; a corrupt entry is deleted and reported as a miss."""
        entry = self._backend.get_entry(key)
        if entry is None:
            return None
        if checksum(key, entry.payload) != entry.checksum:
            self._discard(key)
            return None
        return entry.payload

    def put(self, key: CacheKey, payload: bytes) -> None:
        """Store ``payload`` for ``key``."""
        self._backend.put_entry(key, CacheEntry(payload, checksum(key, payload)), now_ist())

    def get_or_compute[T](
        self,
        key: CacheKey,
        compute: Callable[[], T],
        *,
        encode: Callable[[T], bytes],
        decode: Callable[[bytes], T],
        keep: Callable[[T], bool] | None = None,
    ) -> T:
        """Cached value for ``key``, else ``compute()`` it and cache it.

        ``decode`` should raise for anything it cannot fully validate; any
        exception from it discards the entry and recomputes. ``keep`` (if given)
        is asked before a computed value is stored: False means the value is
        returned but not cached (for example, the input changed meanwhile).
        """
        payload = self.get(key)
        if payload is not None:
            try:
                value = decode(payload)
            except Exception:  # any failure to decode means: do not trust it
                self._discard(key)
            else:
                self.stats.hits += 1
                return value
        self.stats.misses += 1
        value = compute()
        if keep is None or keep(value):
            self.put(key, encode(value))
        return value

    def invalidate_step(self, step: str, keep_version: str) -> int:
        """Delete every result of ``step`` except those of ``keep_version``."""
        return self._backend.delete_step_versions(step, keep_version)

    def _discard(self, key: CacheKey) -> None:
        self._backend.delete_entry(key)
        self.stats.discarded += 1
