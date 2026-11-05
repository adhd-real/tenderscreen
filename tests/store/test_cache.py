from collections.abc import Iterator
from pathlib import Path

import pytest

from tenderscreen.store.cache import ResultCache, checksum
from tenderscreen.store.records import CacheKey
from tenderscreen.store.sqlite import SQLiteBackend
from tests.sqlite_tools import run_sql

SHA = "e" * 64
KEY_V1 = CacheKey(SHA, "count_words", "1")
KEY_V2 = CacheKey(SHA, "count_words", "2")


def encode(value: int) -> bytes:
    return str(value).encode()


def decode(payload: bytes) -> int:
    return int(payload.decode())  # ValueError for anything that is not an integer


class Step:
    """A step that counts how often it really ran."""

    def __init__(self, result: int) -> None:
        self.result = result
        self.runs = 0

    def __call__(self) -> int:
        self.runs += 1
        return self.result


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "store.sqlite3"


@pytest.fixture
def cache(db: Path) -> Iterator[ResultCache]:
    backend = SQLiteBackend(db)
    yield ResultCache(backend)
    backend.close()


def test_second_call_is_a_cache_hit(cache: ResultCache) -> None:
    step = Step(42)
    assert cache.get_or_compute(KEY_V1, step, encode=encode, decode=decode) == 42
    assert cache.get_or_compute(KEY_V1, step, encode=encode, decode=decode) == 42
    assert step.runs == 1
    assert (cache.stats.hits, cache.stats.misses, cache.stats.discarded) == (1, 1, 0)


def test_hit_survives_reopening_the_database(db: Path) -> None:
    step = Step(7)
    first = SQLiteBackend(db)
    ResultCache(first).get_or_compute(KEY_V1, step, encode=encode, decode=decode)
    first.close()
    second = SQLiteBackend(db)
    reopened = ResultCache(second)
    assert reopened.get_or_compute(KEY_V1, step, encode=encode, decode=decode) == 7
    second.close()
    assert step.runs == 1


def test_new_step_version_recomputes_and_drops_old_entry(cache: ResultCache) -> None:
    old, new = Step(1), Step(2)
    cache.get_or_compute(KEY_V1, old, encode=encode, decode=decode)
    assert cache.get_or_compute(KEY_V2, new, encode=encode, decode=decode) == 2
    assert new.runs == 1
    assert cache.get(KEY_V1) is None  # invalidated
    assert cache.get(KEY_V2) == b"2"


def test_tampered_payload_is_recomputed_not_trusted(cache: ResultCache, db: Path) -> None:
    step = Step(42)
    cache.get_or_compute(KEY_V1, step, encode=encode, decode=decode)
    # Flip the stored bytes, keep the old checksum.
    run_sql(db, "UPDATE step_results SET payload = ?", (b"999",))
    assert cache.get_or_compute(KEY_V1, step, encode=encode, decode=decode) == 42
    assert step.runs == 2
    assert cache.stats.discarded == 1
    assert cache.get(KEY_V1) == b"42"  # the fresh result replaced the bad entry


def test_undecodable_payload_is_recomputed_not_trusted(cache: ResultCache, db: Path) -> None:
    step = Step(42)
    cache.put(KEY_V1, b"not a number")  # checksum is valid, content is not
    assert cache.get_or_compute(KEY_V1, step, encode=encode, decode=decode) == 42
    assert step.runs == 1
    assert cache.stats.discarded == 1


def test_get_returns_none_for_missing_and_corrupt(cache: ResultCache, db: Path) -> None:
    assert cache.get(KEY_V1) is None
    cache.put(KEY_V1, b"5")
    run_sql(db, "UPDATE step_results SET checksum = ?", ("0" * 64,))
    assert cache.get(KEY_V1) is None
    assert cache.stats.discarded == 1
    assert run_sql(db, "SELECT COUNT(*) FROM step_results") == [(0,)]


def test_invalidate_step_removes_other_versions(cache: ResultCache) -> None:
    cache.put(KEY_V1, b"1")
    cache.put(CacheKey("f" * 64, "count_words", "2"), b"2")
    assert cache.invalidate_step("count_words", keep_version="2") == 1
    assert cache.get(KEY_V1) is None


def test_compute_errors_are_not_cached(cache: ResultCache) -> None:
    def broken() -> int:
        raise RuntimeError("step failed")

    with pytest.raises(RuntimeError, match="step failed"):
        cache.get_or_compute(KEY_V1, broken, encode=encode, decode=decode)
    assert cache.get(KEY_V1) is None


def test_checksum_is_sha256_hex() -> None:
    assert checksum(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
