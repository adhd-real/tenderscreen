"""Acceptance tests for the data check kit (Task F): synthetic files only."""

import csv
import io
import runpy
import sys
from pathlib import Path

import pytest

from tenderscreen.datacheck.records import COLUMNS, PREFILLED, template_csv
from tenderscreen.datacheck.run import (
    RECORDS_NAME,
    SUMMARY_NAME,
    check,
    list_documents,
    main,
    run,
)
from tenderscreen.documents.ocr import Tesseract
from tests.documents.pdf_factory import ENGLISH_NOTICE, ScannedPage, TextPage, build_pdf


@pytest.fixture
def manual(tmp_path: Path) -> Path:
    folder = tmp_path / "manual"
    (folder / "gem").mkdir(parents=True)
    (folder / RECORDS_NAME).write_text(template_csv(), encoding="utf-8")
    (folder / "README.md").write_text("readme", encoding="utf-8")
    build_pdf(folder / "cppp_award_1.pdf", [TextPage(), TextPage()])
    build_pdf(folder / "gem" / "gem_bid_7.pdf", [ScannedPage()])
    (folder / "punjab_aoc.html").write_text(
        "<html><body>Award of contract to Synthetic Builders for road works</body></html>",
        encoding="utf-8",
    )
    return folder


def rows(folder: Path) -> list[dict[str, str]]:
    text = (folder / RECORDS_NAME).read_text(encoding="utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def by_file(folder: Path) -> dict[str, dict[str, str]]:
    return {row["file"]: row for row in rows(folder)}


def test_list_documents_skips_kit_files_and_hidden(manual: Path) -> None:
    (manual / ".hidden.pdf").write_bytes(b"x")
    (manual / ".git-stuff").mkdir()
    (manual / ".git-stuff" / "a.pdf").write_bytes(b"x")
    (manual / SUMMARY_NAME).write_text("old", encoding="utf-8")
    assert list_documents(manual) == ["cppp_award_1.pdf", "gem/gem_bid_7.pdf", "punjab_aoc.html"]


def test_first_run_adds_rows_and_prefills(manual: Path) -> None:
    output = run(manual, ocr=False, store_root=manual / "unused")
    records = by_file(manual)
    assert set(records) == {"cppp_award_1.pdf", "gem/gem_bid_7.pdf", "punjab_aoc.html"}
    assert records["cppp_award_1.pdf"]["file_format"] == "pdf"
    assert records["cppp_award_1.pdf"]["text_layer_or_scanned"] == "text layer"
    assert records["cppp_award_1.pdf"]["page_count"] == "2"
    assert records["cppp_award_1.pdf"]["language"] == "english"
    assert records["gem/gem_bid_7.pdf"]["text_layer_or_scanned"] == "scanned"
    assert records["gem/gem_bid_7.pdf"]["language"] == ""  # needs OCR
    assert records["punjab_aoc.html"]["file_format"] == "html"
    assert "3 new rows" in output
    summary = (manual / SUMMARY_NAME).read_text(encoding="utf-8")
    assert "**3 records**" in summary
    assert "`gem/gem_bid_7.pdf`: language not checked" in summary


@pytest.mark.slow
def test_rerun_never_overwrites_manual_entries(manual: Path) -> None:
    """Acceptance: whatever a person typed survives every later run."""
    run(manual, ocr=False, store_root=manual / "unused")
    typed = rows(manual)
    for row in typed:
        row["portal"] = "GeM" if row["file"].startswith("gem/") else "CPPP"
        row["has_award_date"] = "y"
        row["notes"] = "checked by hand, 05/11/2026"
    typed[0]["language"] = "hindi (typed by hand)"  # disagrees with the kit on purpose
    typed[0]["page_count"] = "99"
    typed[1]["file_format"] = "PDF"
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(typed)
    (manual / RECORDS_NAME).write_text(buffer.getvalue(), encoding="utf-8")

    for _ in range(2):
        output = run(manual, ocr=False, store_root=manual / "unused")
        assert rows(manual) == typed
        assert "records.csv: no changes." in output


@pytest.mark.slow
def test_new_file_gets_a_row_and_existing_rows_keep_their_order(manual: Path) -> None:
    run(manual, ocr=False, store_root=manual / "unused")
    before = [r["file"] for r in rows(manual)]
    build_pdf(manual / "aaa_first_alphabetically.pdf", [TextPage()])
    run(manual, ocr=False, store_root=manual / "unused")
    after = [r["file"] for r in rows(manual)]
    assert after == [*before, "aaa_first_alphabetically.pdf"]


def test_rows_without_files_are_kept_and_reported(manual: Path) -> None:
    (manual / RECORDS_NAME).write_text(
        template_csv() + "gone.pdf,CPPP\n,GeM,https://example.org,,award\n", encoding="utf-8"
    )
    result = check(manual, ocr=False)
    assert result.problems[-1] == "records.csv names `gone.pdf`, but there is no such file"
    assert [r["file"] for r in result.records.rows][:2] == ["gone.pdf", ""]


def test_two_rows_for_one_file_are_both_prefilled(manual: Path) -> None:
    (manual / RECORDS_NAME).write_text(
        template_csv() + "cppp_award_1.pdf,CPPP\ncppp_award_1.pdf,CPPP,second record\n",
        encoding="utf-8",
    )
    result = check(manual, ocr=False)
    pdf_rows = [r for r in result.records.rows if r["file"] == "cppp_award_1.pdf"]
    assert [r["page_count"] for r in pdf_rows] == ["2", "2"]


def test_unchanged_file_is_not_rewritten(manual: Path) -> None:
    run(manual, ocr=False, store_root=manual / "unused")
    path = manual / RECORDS_NAME
    # Same content, but quoted in a style the csv module would not write itself.
    text = path.read_text(encoding="utf-8").replace(",english,", ',"english",', 1)
    path.write_text(text, encoding="utf-8")
    run(manual, ocr=False, store_root=manual / "unused")
    assert path.read_text(encoding="utf-8") == text


def test_main_no_ocr(manual: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--manual-dir", str(manual), "--no-ocr"]) == 0
    assert "Manual data check: summary" in capsys.readouterr().out


def test_main_missing_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--manual-dir", str(tmp_path / "nope"), "--no-ocr"]) == 2
    assert "is not a folder" in capsys.readouterr().err


def test_main_broken_csv_changes_nothing(manual: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken = template_csv() + "a.pdf,CPPP" + "," * len(COLUMNS) + "extra\n"
    (manual / RECORDS_NAME).write_text(broken, encoding="utf-8")
    assert main(["--manual-dir", str(manual), "--no-ocr"]) == 2
    assert "records.csv was not changed" in capsys.readouterr().err
    assert (manual / RECORDS_NAME).read_text(encoding="utf-8") == broken
    assert not (manual / SUMMARY_NAME).exists()


def test_main_non_utf8_csv(manual: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (manual / RECORDS_NAME).write_bytes(b"file,portal\n\xff\xfe,x\n")
    assert main(["--manual-dir", str(manual), "--no-ocr"]) == 2
    err = capsys.readouterr().err
    assert "not UTF-8 text" in err
    assert "CSV UTF-8 (Comma delimited)" in err
    assert "records.csv was not changed" in err


def test_files_with_every_kit_cell_filled_are_not_inspected_again(
    manual: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(manual, ocr=False, store_root=manual / "unused")
    rows_now = rows(manual)
    for row in rows_now:
        for column in PREFILLED:
            row[column] = row[column] or "n/a (typed by hand)"
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows_now)
    (manual / RECORDS_NAME).write_text(buffer.getvalue(), encoding="utf-8")
    inspected: list[Path] = []

    def recording(path: Path, **kwargs: object) -> object:
        inspected.append(path)
        raise AssertionError("inspected a file whose kit cells are all filled")

    monkeypatch.setattr("tenderscreen.datacheck.run.inspect_file", recording)
    run(manual, ocr=False, store_root=manual / "unused")
    assert inspected == []


# Moved from test_ocr_path.py, which mirrored no module.


@pytest.mark.slow
def test_second_run_uses_the_cache_not_ocr(
    tmp_path: Path, tesseract: Tesseract, monkeypatch: pytest.MonkeyPatch
) -> None:
    manual = tmp_path / "manual"
    manual.mkdir()
    (manual / RECORDS_NAME).write_text(template_csv(), encoding="utf-8")
    build_pdf(manual / "scan.pdf", [ScannedPage(ENGLISH_NOTICE * 2)])
    raw = tmp_path / "raw"
    assert "1 new rows" in run(manual, ocr=True, store_root=raw)
    (manual / RECORDS_NAME).write_text(template_csv(), encoding="utf-8")  # start over

    def no_ocr(*args: object, **kwargs: object) -> None:
        raise AssertionError("OCR ran again despite a cached result")

    monkeypatch.setattr("tenderscreen.documents.extract.run_tasks", no_ocr)
    run(manual, ocr=True, store_root=raw)
    assert "scan.pdf,,,,,,,english,pdf,scanned,1," in (manual / RECORDS_NAME).read_text("utf-8")


@pytest.mark.filterwarnings("ignore:.*found in sys.modules:RuntimeWarning")
def test_main_module_in_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manual = tmp_path / "manual"
    manual.mkdir()
    monkeypatch.setattr(sys, "argv", ["datacheck", "--manual-dir", str(manual), "--no-ocr"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("tenderscreen.datacheck", run_name="__main__")
    assert exc.value.code == 0
    assert (manual / RECORDS_NAME).read_text(encoding="utf-8") == template_csv()


# Found in review.


@pytest.mark.slow
def test_file_name_with_surrounding_spaces_gets_one_row_only(manual: Path) -> None:
    build_pdf(manual / "spaced.pdf ", [TextPage()])
    run(manual, ocr=False, store_root=manual / "unused")
    output = run(manual, ocr=False, store_root=manual / "unused")
    assert "records.csv: no changes." in output
    names = [r["file"] for r in rows(manual)]
    assert names.count("spaced.pdf ") == 1
    assert "no such file" not in (manual / SUMMARY_NAME).read_text(encoding="utf-8")


def test_typed_name_with_spaces_matches_its_file(manual: Path) -> None:
    (manual / RECORDS_NAME).write_text(
        template_csv() + " cppp_award_1.pdf ,CPPP\n", encoding="utf-8"
    )
    result = check(manual, ocr=False)
    names = [r["file"] for r in result.records.rows]
    assert names.count(" cppp_award_1.pdf ") == 1
    assert "cppp_award_1.pdf" not in names  # no second row for the same file
    assert result.records.rows[0]["page_count"] == "2"


def test_odd_file_names_cannot_break_the_summary(manual: Path) -> None:
    (manual / "odd`name|1.html").write_text("<p>Award</p>", encoding="utf-8")
    (manual / RECORDS_NAME).write_text(template_csv() + "gone`x.pdf\n", encoding="utf-8")
    run(manual, ocr=False, store_root=manual / "unused")
    summary = (manual / SUMMARY_NAME).read_text(encoding="utf-8")
    assert "records.csv names `gone'x.pdf`, but there is no such file" in summary


def test_rerun_leaves_the_summary_byte_for_byte_unchanged(manual: Path) -> None:
    run(manual, ocr=False, store_root=manual / "unused")
    first = (manual / SUMMARY_NAME).read_bytes()
    run(manual, ocr=False, store_root=manual / "unused")
    assert (manual / SUMMARY_NAME).read_bytes() == first


def test_records_file_keeps_its_permissions(manual: Path) -> None:
    path = manual / RECORDS_NAME
    path.chmod(0o664)
    run(manual, ocr=False, store_root=manual / "unused")
    assert path.stat().st_mode & 0o777 == 0o664


def test_main_broken_quoting_changes_nothing(
    manual: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = template_csv() + 'a.pdf,"CPPP\nb.pdf,GeM\n'
    (manual / RECORDS_NAME).write_text(broken, encoding="utf-8")
    assert main(["--manual-dir", str(manual), "--no-ocr"]) == 2
    err = capsys.readouterr().err
    assert "broken CSV at or before line 3" in err
    assert "records.csv was not changed" in err
    assert (manual / RECORDS_NAME).read_text(encoding="utf-8") == broken
