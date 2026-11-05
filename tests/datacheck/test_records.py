import csv
import io
import os
import stat
from pathlib import Path

import pytest

from tenderscreen.datacheck.records import (
    COLUMNS,
    FIELD_COLUMNS,
    NEW_FILE_MODE,
    PREFILLED,
    Records,
    RecordsError,
    _file_mode,
    _parse_csv,
    fill_blanks,
    read_records,
    template_csv,
    write_records,
)

HEADER = ",".join(COLUMNS)


def test_template_has_every_required_column() -> None:
    assert template_csv() == HEADER + "\n"
    for column in ("portal", "url", "date_found", "notice_type", "captcha_needed"):
        assert column in COLUMNS
    assert "robots_txt_notes" in COLUMNS
    assert set(PREFILLED) == {"file_format", "text_layer_or_scanned", "page_count", "language"}
    assert FIELD_COLUMNS == (
        "has_award_date",
        "has_successful_bidder_name",
        "has_contract_value",
        "has_number_of_bids",
        "has_loa_date",
        "has_pbg_percentage_or_deadline",
        "has_contact_details",
    )


def test_committed_records_file_has_every_column() -> None:
    """Only the header is checked: the rows (and any extra columns) are the user's data,
    and filling them in must never break this test (found in review)."""
    committed = Path(__file__).resolve().parents[2] / "data" / "manual" / "records.csv"
    first_line = committed.read_text(encoding="utf-8-sig").splitlines()[0]
    header = next(csv.reader(io.StringIO(first_line)))
    assert set(COLUMNS) <= {name.strip() for name in header}


def test_missing_file_gives_empty_template_table(tmp_path: Path) -> None:
    records = read_records(tmp_path / "records.csv")
    assert (records.header, records.rows, records.added_columns) == (list(COLUMNS), [], [])
    assert not records.existed
    path = tmp_path / "records.csv"
    write_records(path, records)
    assert path.read_text(encoding="utf-8") == template_csv()
    assert read_records(path).existed


def test_round_trip_keeps_bom_crlf_extra_columns_and_order(tmp_path: Path) -> None:
    path = tmp_path / "records.csv"
    content = '\ufeffmy_col,file,portal\r\nkeep me,a.pdf,GeM\r\n\r\n,b.pdf,"CPPP, central"\r\n'
    path.write_bytes(content.encode("utf-8"))
    records = read_records(path)
    assert records.bom
    assert records.line_ending == "\r\n"
    assert records.header[:3] == ["my_col", "file", "portal"]
    assert records.added_columns == [c for c in COLUMNS if c not in ("file", "portal")]
    assert [r["file"] for r in records.rows] == ["a.pdf", "b.pdf"]  # blank line dropped
    write_records(path, records)
    written = path.read_bytes().decode("utf-8")
    assert written.startswith("\ufeffmy_col,file,portal,")
    assert "\r\nkeep me,a.pdf,GeM," in written
    assert '"CPPP, central"' in written
    assert read_records(path).rows == records.rows


def test_short_rows_are_padded(tmp_path: Path) -> None:
    path = tmp_path / "records.csv"
    path.write_text("file,portal,notes\na.pdf\n", encoding="utf-8")
    assert read_records(path).rows[0]["notes"] == ""


def test_empty_file_gives_template_header(tmp_path: Path) -> None:
    path = tmp_path / "records.csv"
    path.write_text("", encoding="utf-8")
    assert read_records(path).header == list(COLUMNS)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("file,portal,portal\n", "blank or repeated"),
        ("file,,portal\n", "blank or repeated"),
        ("portal,url\nGeM,x\n", "'file' column is missing"),
        ("file,portal\na.pdf,GeM,extra\n", "line 2: 3 cells but only 2 columns"),
    ],
)
def test_malformed_csv_is_refused(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "records.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RecordsError, match=message):
        read_records(path)


def test_fill_blanks_never_overwrites() -> None:
    row = {"file_format": "PDF (typed by hand)", "language": "  ", "page_count": ""}
    filled = fill_blanks(row, {"file_format": "pdf", "language": "hindi", "page_count": ""})
    assert filled == ["language"]
    assert row == {"file_format": "PDF (typed by hand)", "language": "hindi", "page_count": ""}


def test_fill_blanks_adds_missing_keys() -> None:
    row: dict[str, str] = {}
    assert fill_blanks(row, {"language": "english"}) == ["language"]


def test_failed_write_keeps_the_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "records.csv"
    path.write_text("file\nold.pdf\n", encoding="utf-8")

    def fail(src: str, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("tenderscreen.datacheck.records.os.replace", fail)
    with pytest.raises(OSError, match="disk full"):
        write_records(path, Records(["file"], [{"file": "new.pdf"}]))
    assert path.read_text(encoding="utf-8") == "file\nold.pdf\n"
    assert [p.name for p in tmp_path.iterdir()] == ["records.csv"]


# Found in review.


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('file,portal\na.pdf,"GeM"x\n', "at or before line 2"),
        ('file,portal\na.pdf,"GeM\nb.pdf,CPPP\n', "at or before line 3"),
        ('file,portal\n"a\nb.pdf",GeM\nc.pdf,"x"y\n', "at or before line 4"),
    ],
)
def test_broken_quoting_is_refused_not_merged(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "records.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RecordsError, match=f"broken CSV {message}.*stray double quote"):
        read_records(path)


def test_parse_csv_reports_the_line_each_row_ends_on() -> None:
    rows = _parse_csv("r.csv", 'file,notes\na.pdf,"two\nlines"\nb.pdf,x\n')
    assert rows == [(1, ["file", "notes"]), (3, ["a.pdf", "two\nlines"]), (4, ["b.pdf", "x"])]


def test_quotes_inside_unquoted_cells_are_kept() -> None:
    assert _parse_csv("r.csv", 'file,notes\na.pdf,6" pipe\n')[1] == (2, ["a.pdf", '6" pipe'])


def test_excel_trailing_empty_columns_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "records.csv"
    path.write_text("file,portal,,\na.pdf,GeM,,\nb.pdf,CPPP\n", encoding="utf-8")
    records = read_records(path)
    assert records.header[:2] == ["file", "portal"]
    assert "" not in records.header
    assert [(r["file"], r["portal"]) for r in records.rows] == [("a.pdf", "GeM"), ("b.pdf", "CPPP")]


def test_value_under_an_unnamed_column_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "records.csv"
    path.write_text("file,portal,,\na.pdf,GeM,,typed here\n", encoding="utf-8")
    with pytest.raises(RecordsError, match="line 2: a value in a column with no name"):
        read_records(path)


def test_row_wider_than_the_header_row_is_still_refused(tmp_path: Path) -> None:
    path = tmp_path / "records.csv"
    path.write_text("file,portal,\na.pdf,GeM,,\n", encoding="utf-8")
    with pytest.raises(RecordsError, match="line 2: 4 cells but only 3 columns"):
        read_records(path)


def test_write_keeps_the_file_mode(tmp_path: Path) -> None:
    path = tmp_path / "records.csv"
    path.write_text("file\nold.pdf\n", encoding="utf-8")
    path.chmod(0o640)
    write_records(path, Records(["file"], [{"file": "new.pdf"}]))
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert path.read_text(encoding="utf-8") == "file\nnew.pdf\n"


def test_new_file_is_readable_not_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "records.csv"
    write_records(path, Records(["file"], []))
    assert stat.S_IMODE(path.stat().st_mode) == NEW_FILE_MODE == 0o644


def test_file_mode(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    assert _file_mode(path) == NEW_FILE_MODE
    path.write_text("", encoding="utf-8")
    path.chmod(0o600)
    assert _file_mode(path) == 0o600


def test_write_is_flushed_to_disk_and_the_folder_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[object] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        synced.append(fd)
        real_fsync(fd)

    folders: list[Path] = []
    monkeypatch.setattr("tenderscreen.datacheck.records.os.fsync", recording_fsync)
    monkeypatch.setattr("tenderscreen.datacheck.records.sync_directory", folders.append)
    write_records(tmp_path / "records.csv", Records(["file"], []))
    assert len(synced) == 1
    assert folders == [tmp_path]
