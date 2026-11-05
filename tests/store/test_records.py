from datetime import UTC, datetime

import pytest

from tenderscreen.ist import IST
from tenderscreen.store.records import CacheKey, Source, check_sha256

SHA = "a" * 64


def test_check_sha256_accepts_lowercase_hex() -> None:
    assert check_sha256(SHA) == SHA


@pytest.mark.parametrize("value", ["", "A" * 64, "a" * 63, "g" * 64, "../" + "a" * 61])
def test_check_sha256_rejects_anything_else(value: str) -> None:
    with pytest.raises(ValueError, match="sha256"):
        check_sha256(value)


def test_source_converts_fetch_time_to_ist() -> None:
    source = Source("https://eprocure.gov.in/x.pdf", datetime(2026, 10, 15, 6, 0, tzinfo=UTC))
    assert source.fetched_at.tzinfo is IST
    assert (source.fetched_at.hour, source.fetched_at.minute) == (11, 30)


def test_source_refuses_naive_fetch_time() -> None:
    with pytest.raises(ValueError, match="naive"):
        Source("https://example.org/a.pdf", datetime(2026, 10, 15))  # noqa: DTZ001 - testing the guard


@pytest.mark.parametrize(
    "url", ["", "no-scheme.pdf", "has space://x", "https://x/" + "a" * 2048, "1http://x"]
)
def test_source_refuses_bad_urls(url: str) -> None:
    with pytest.raises(ValueError, match="URL"):
        Source(url, datetime(2026, 10, 15, tzinfo=IST))


def test_source_accepts_non_http_schemes_for_manual_uploads() -> None:
    assert Source("file:data/manual/x.pdf", datetime(2026, 10, 15, tzinfo=IST)).url


def test_cache_key_validation() -> None:
    assert CacheKey(SHA, "extract_pages", "1").step == "extract_pages"
    with pytest.raises(ValueError, match="sha256"):
        CacheKey("bad", "extract_pages", "1")
    with pytest.raises(ValueError, match="step name and a step version"):
        CacheKey(SHA, "", "1")
    with pytest.raises(ValueError, match="step name and a step version"):
        CacheKey(SHA, "extract_pages", "")
