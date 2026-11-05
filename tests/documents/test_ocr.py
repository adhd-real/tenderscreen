import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tenderscreen.documents import ocr
from tenderscreen.documents.errors import OcrUnavailableError
from tenderscreen.documents.ocr import (
    OcrOutcome,
    OcrTask,
    Tesseract,
    find_tesseract,
    mean_word_confidence,
    ocr_page,
    ocr_page_json,
    outcome_from_json,
    render_scale,
    require_tesseract,
    tesseract_fingerprint,
)
from tenderscreen.documents.sandbox import Json
from tests.documents.fake_tesseract import FAKE_TSV_HEADER, write_fake_tesseract
from tests.documents.pdf_factory import AWARD_SCAN, HINDI_NOTICE, ScannedPage, build_pdf

A4_POINTS = (595.0, 842.0)


def make_task(pdf: Path, tesseract_path: str, timeout_s: float = 60.0) -> OcrTask:
    work_dir = pdf.parent / f"work-{pdf.stem}"
    work_dir.mkdir(exist_ok=True)
    return OcrTask(
        pdf_path=str(pdf),
        page_index=0,
        tesseract_path=tesseract_path,
        languages="eng+hin",
        dpi=300,
        max_pixels=50_000_000,
        timeout_s=timeout_s,
        work_dir=str(work_dir),
    )


def tsv_rows(*rows: str) -> str:
    return "\n".join([FAKE_TSV_HEADER, *rows]) + "\n"


def word(conf: str, text: str) -> str:
    return f"5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t{conf}\t{text}"


def test_render_scale_uses_dpi() -> None:
    assert render_scale(*A4_POINTS, dpi=300, max_pixels=50_000_000) == pytest.approx(300 / 72)


def test_render_scale_caps_huge_pages() -> None:
    scale = render_scale(3370.0, 2384.0, dpi=300, max_pixels=10_000_000)  # A0 poster
    assert (3370 * scale) * (2384 * scale) == pytest.approx(10_000_000)


def test_mean_word_confidence() -> None:
    tsv = tsv_rows(
        "1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t",  # page row, not a word
        word("90.5", "AWARD"),
        word("70.5", "OF"),
        word("-1", ""),  # empty word
        word("-1", "x"),  # unscored word
        word("oops", "bad"),  # malformed confidence
        "5\t1\ttoo\tfew",  # malformed row
    )
    assert mean_word_confidence(tsv) == pytest.approx(80.5)


@pytest.mark.parametrize("tsv", ["", "no\theader\there\n", tsv_rows(word("-1", "x"))])
def test_mean_word_confidence_none_without_scored_words(tsv: str) -> None:
    assert mean_word_confidence(tsv) is None


def test_find_tesseract_missing_binary() -> None:
    assert find_tesseract("no-such-tesseract-binary") is None


def test_find_tesseract_reads_version_and_languages(tmp_path: Path) -> None:
    fake = write_fake_tesseract(tmp_path, "exit 1", langs="eng\nhin\nosd")
    found = find_tesseract(str(fake))
    assert found is not None
    assert (found.path, found.version, found.languages) == (
        str(fake),
        "tesseract 0.0.0-fake",
        frozenset({"eng", "hin", "osd"}),
    )
    assert len(found.fingerprint) == 16


def test_find_tesseract_broken_binary(tmp_path: Path) -> None:
    broken = tmp_path / "tesseract"
    broken.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    broken.chmod(0o755)
    assert find_tesseract(str(broken)) is None


def test_require_tesseract_names_missing_binary() -> None:
    with pytest.raises(OcrUnavailableError, match="'no-such-binary' program was not found"):
        require_tesseract("eng+hin", "no-such-binary")


def test_require_tesseract_names_missing_language(tmp_path: Path) -> None:
    fake = write_fake_tesseract(tmp_path, "exit 1", langs="eng")
    with pytest.raises(OcrUnavailableError, match="language data missing for hin"):
        require_tesseract("eng+hin", str(fake))


def test_tesseract_env_is_minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSDATA_PREFIX", "/opt/tessdata")
    monkeypatch.setenv("SOME_SECRET_TOKEN", "do-not-pass")
    env = ocr._tesseract_env()
    assert env == {
        "PATH": os.environ["PATH"],
        "OMP_THREAD_LIMIT": "1",
        "TESSDATA_PREFIX": "/opt/tessdata",
    }


def test_ocr_page_with_fake_tesseract(tmp_path: Path) -> None:
    pdf = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    tsv = tsv_rows(word("88", "AWARD")).replace("\t", "\\t").replace("\n", "\\n")
    fake = write_fake_tesseract(
        tmp_path, f"""printf 'AWARD\\n\\f' > "$2.txt"; printf '{tsv}' > "$2.tsv\""""
    )
    outcome = ocr_page(make_task(pdf, str(fake)))
    assert outcome == OcrOutcome(0, text="AWARD", confidence=88.0, worker_pid=os.getpid())


def test_ocr_page_reports_tesseract_failure(tmp_path: Path) -> None:
    pdf = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    fake = write_fake_tesseract(tmp_path, "echo 'boom: bad image' >&2; exit 1")
    outcome = ocr_page(make_task(pdf, str(fake)))
    assert outcome.error == "tesseract: boom: bad image"
    assert not outcome.timed_out


def test_ocr_page_reports_silent_tesseract_failure(tmp_path: Path) -> None:
    pdf = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    fake = write_fake_tesseract(tmp_path, "exit 4")
    assert ocr_page(make_task(pdf, str(fake))).error == "tesseract: exit code 4"


def test_ocr_page_times_out_and_stops_tesseract(tmp_path: Path) -> None:
    pdf = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    fake = write_fake_tesseract(tmp_path, "exec sleep 30")
    outcome = ocr_page(make_task(pdf, str(fake), timeout_s=1.0))
    assert outcome.timed_out
    assert outcome.text == ""


def test_ocr_page_out_of_time_before_tesseract_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    ticks = iter([0.0, 100.0])
    clock = SimpleNamespace(monotonic=lambda: next(ticks))
    monkeypatch.setattr("tenderscreen.documents.ocr.time", clock)
    outcome = ocr_page(make_task(pdf, "/bin/false", timeout_s=60.0))
    assert outcome.timed_out


def test_ocr_page_reports_render_failure(tmp_path: Path) -> None:
    missing = tmp_path / "gone.pdf"
    outcome = ocr_page(make_task(missing, "/bin/false"))
    assert outcome.error is not None
    assert outcome.error.startswith("rendering failed")


@pytest.mark.slow
def test_ocr_page_with_real_tesseract(tmp_path: Path, tesseract: Tesseract) -> None:
    pdf = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    outcome = ocr_page(make_task(pdf, tesseract.path))
    assert outcome.error is None
    for line in AWARD_SCAN:
        assert line in outcome.text
    assert outcome.confidence is not None
    assert outcome.confidence > 80


@pytest.mark.slow
def test_ocr_page_reads_hindi(tmp_path: Path, tesseract: Tesseract, devanagari_font: str) -> None:
    pdf = build_pdf(tmp_path / "hindi.pdf", [ScannedPage(HINDI_NOTICE, font_path=devanagari_font)])
    outcome = ocr_page(make_task(pdf, tesseract.path))
    assert outcome.error is None
    for line in HINDI_NOTICE:
        assert line in outcome.text


def test_find_tesseract_caches_only_successes(tmp_path: Path) -> None:
    broken = tmp_path / "tesseract"
    broken.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    broken.chmod(0o755)
    assert find_tesseract(str(broken)) is None
    working = write_fake_tesseract(tmp_path, "exit 1")  # same path, now it works
    found = find_tesseract(str(working))
    assert found is not None
    assert find_tesseract(str(working)) is found


def test_find_tesseract_empty_version_output(tmp_path: Path) -> None:
    script = tmp_path / "tesseract"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    assert find_tesseract(str(script)) is None


def test_find_tesseract_unrunnable_file(tmp_path: Path) -> None:
    script = tmp_path / "tesseract"
    script.write_bytes(b"\x7fELF but not really a program")
    script.chmod(0o755)
    assert find_tesseract(str(script)) is None


def test_find_tesseract_that_hangs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tenderscreen.documents.ocr._VERSION_TIMEOUT_S", 0.5)
    script = tmp_path / "tesseract"
    script.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    script.chmod(0o755)
    assert find_tesseract(str(script)) is None


def test_ocr_page_missing_output_files(tmp_path: Path) -> None:
    pdf = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    fake = write_fake_tesseract(tmp_path, "exit 0")  # claims success, writes nothing
    outcome = ocr_page(make_task(pdf, str(fake)))
    assert outcome.error is not None
    assert outcome.error.startswith("reading tesseract output")


def test_ocr_page_json_round_trip(tmp_path: Path) -> None:
    pdf = build_pdf(tmp_path / "scan.pdf", [ScannedPage()])
    fake = write_fake_tesseract(tmp_path, "echo 'boom' >&2; exit 1")
    answer = ocr_page_json(make_task(pdf, str(fake)))
    assert outcome_from_json(answer) == ocr_page(make_task(pdf, str(fake)))


GOOD_ANSWER: dict[str, Json] = {
    "page_index": 2,
    "text": "AWARD",
    "confidence": 91.5,
    "error": None,
    "timed_out": False,
    "worker_pid": 123,
}


def test_outcome_from_json_accepts_a_valid_answer() -> None:
    assert outcome_from_json(GOOD_ANSWER) == OcrOutcome(2, "AWARD", 91.5, None, False, 123)
    assert outcome_from_json({**GOOD_ANSWER, "confidence": 90}).confidence == 90.0
    assert outcome_from_json({**GOOD_ANSWER, "confidence": None}).confidence is None


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {**GOOD_ANSWER, "extra": 1},
        {k: v for k, v in GOOD_ANSWER.items() if k != "text"},
        {**GOOD_ANSWER, "page_index": -1},
        {**GOOD_ANSWER, "page_index": True},
        {**GOOD_ANSWER, "text": 5},
        {**GOOD_ANSWER, "timed_out": "no"},
        {**GOOD_ANSWER, "error": 7},
        {**GOOD_ANSWER, "worker_pid": "x"},
        {**GOOD_ANSWER, "confidence": "high"},
        {**GOOD_ANSWER, "confidence": True},
        {**GOOD_ANSWER, "confidence": 101},
    ],
)
def test_outcome_from_json_rejects_malformed_answers(bad: Json) -> None:
    with pytest.raises(ValueError, match="OCR answer"):
        outcome_from_json(bad)


def test_fingerprint_changes_when_a_language_file_changes(tmp_path: Path) -> None:
    model = tmp_path / "eng.traineddata"
    model.write_bytes(b"model v1")
    header = f'List of available languages in "{tmp_path}/" (1):\neng\n'
    first = tesseract_fingerprint("tesseract 5", header, frozenset({"eng"}))
    assert first == tesseract_fingerprint("tesseract 5", header, frozenset({"eng"}))
    model.write_bytes(b"model version 2")
    assert tesseract_fingerprint("tesseract 5", header, frozenset({"eng"})) != first
    assert tesseract_fingerprint("tesseract 5.1", header, frozenset({"eng"})) != first


def test_fingerprint_without_language_files_or_folder() -> None:
    missing = tesseract_fingerprint("v", "List of languages (1):\neng\n", frozenset({"eng"}))
    assert len(missing) == 16
    assert tesseract_fingerprint("v", "", frozenset()) != missing
