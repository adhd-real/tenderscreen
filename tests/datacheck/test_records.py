from pathlib import Path

import pytest

from tenderscreen.datacheck.records import (
    COLUMNS,
    FIELD_COLUMNS,
    PREFILLED,
    Records,
    RecordsError,
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


def test_committed_template_matches_columns() -> None:
    committed = Path(__file__).resolve().parents[2] / "data" / "manual" / "records.csv"
    assert committed.read_text(encoding="utf-8") == template_csv()


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
