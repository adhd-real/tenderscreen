from collections.abc import Callable

import pytest

from tenderscreen.documents.models import (
    REPLACEMENT_CHARACTER,
    ExtractionLimits,
    ExtractionMethod,
    ExtractionSettings,
    PageQuality,
    assess_quality,
    meaningful_chars,
    suspicious_share,
    usable_cpus,
)

SETTINGS = ExtractionSettings()
PRIVATE_USE = chr(0xE000)


def test_default_limits_match_the_spec() -> None:
    limits = ExtractionLimits()
    assert limits.max_file_bytes == 100 * 1024 * 1024
    assert limits.max_pages == 1000
    assert limits.page_timeout_s == 60
    assert limits.max_memory_bytes == 1024 * 1024 * 1024
    assert limits.max_document_s == 1800


@pytest.mark.parametrize(
    "make",
    [
        lambda: ExtractionLimits(max_file_bytes=0),
        lambda: ExtractionLimits(max_pages=-1),
        lambda: ExtractionLimits(page_timeout_s=0),
        lambda: ExtractionLimits(max_memory_bytes=0),
        lambda: ExtractionLimits(max_document_s=0),
        lambda: ExtractionLimits(page_timeout_s=float("nan")),
        lambda: ExtractionLimits(max_document_s=float("inf")),
    ],
)
def test_limits_must_be_positive(make: Callable[[], ExtractionLimits]) -> None:
    with pytest.raises(ValueError, match="positive"):
        make()


@pytest.mark.parametrize(
    "make",
    [
        lambda: ExtractionSettings(languages=""),
        lambda: ExtractionSettings(min_text_chars=0),
        lambda: ExtractionSettings(ocr_dpi=10),
        lambda: ExtractionSettings(workers=0),
    ],
)
def test_settings_reject_invalid_values(make: Callable[[], ExtractionSettings]) -> None:
    with pytest.raises(ValueError, match="ExtractionSettings"):
        make()


def test_worker_count_never_exceeds_tasks_or_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ExtractionSettings(workers=4).worker_count(2) == 2
    assert ExtractionSettings(workers=2).worker_count(10) == 2
    assert ExtractionSettings(workers=3).worker_count(0) == 1
    monkeypatch.setattr("os.sched_getaffinity", lambda pid: {0, 1, 2}, raising=False)
    assert ExtractionSettings().worker_count(10) == 3


def test_usable_cpus_respects_affinity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.sched_getaffinity", lambda pid: {5}, raising=False)
    assert usable_cpus() == 1


def test_usable_cpus_without_affinity_support(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr("os.sched_getaffinity", raising=False)
    monkeypatch.setattr("os.cpu_count", lambda: None)
    assert usable_cpus() == 2
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    assert usable_cpus() == 8


def test_meaningful_chars_counts_letters_digits_and_vowel_signs() -> None:
    assert meaningful_chars("Rs. 25,000/-") == 7
    assert meaningful_chars("निविदा") == 6  # 3 consonants + 3 vowel signs
    assert meaningful_chars("  \n--//..  ") == 0
    assert meaningful_chars(PRIVATE_USE * 5) == 0


def test_suspicious_share() -> None:
    assert suspicious_share("") == 0.0
    assert suspicious_share("   \n") == 0.0
    assert suspicious_share("clean text") == 0.0
    assert suspicious_share(f"ab{REPLACEMENT_CHARACTER}{PRIVATE_USE}") == 0.5
    assert suspicious_share("a\x01") == 0.5
    assert suspicious_share("a\tb\nc") == 0.0


@pytest.mark.parametrize(
    ("text", "method", "confidence", "expected"),
    [
        ("", ExtractionMethod.TEXT_LAYER, None, PageQuality.EMPTY),
        ("  -- ", ExtractionMethod.OCR, 95.0, PageQuality.EMPTY),
        ("Tender notice", ExtractionMethod.TEXT_LAYER, None, PageQuality.GOOD),
        ("Tender notice", ExtractionMethod.OCR, 91.5, PageQuality.GOOD),
        ("Tender notice", ExtractionMethod.OCR, 60.0, PageQuality.GOOD),
        ("Tender notice", ExtractionMethod.OCR, 59.9, PageQuality.LOW),
        ("Tender notice", ExtractionMethod.OCR, None, PageQuality.LOW),
        ("Tend" + PRIVATE_USE * 3, ExtractionMethod.TEXT_LAYER, None, PageQuality.LOW),
    ],
)
def test_assess_quality(
    text: str, method: ExtractionMethod, confidence: float | None, expected: PageQuality
) -> None:
    assert assess_quality(text, method, confidence, SETTINGS) is expected


def test_enum_values_are_the_documented_strings() -> None:
    assert [m.value for m in ExtractionMethod] == ["text_layer", "ocr"]
    assert [q.value for q in PageQuality] == ["good", "low", "empty"]
