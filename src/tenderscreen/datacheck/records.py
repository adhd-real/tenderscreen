"""``data/manual/records.csv``: read it, fill only blank cells, write it back safely.

The kit never overwrites a non-empty cell, never reorders or drops rows, keeps
any extra columns you added, and keeps the file's encoding mark (BOM) and line
endings. If the CSV is malformed (duplicate headers, a row with more cells than
the header) it stops instead of guessing, so nothing you typed can be lost.
"""

import csv
import io
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

# One row per record. "file" links a row to a document in data/manual/.
COLUMNS: Final = (
    "file",
    "portal",
    "url",
    "date_found",
    "notice_type",
    "captcha_needed",
    "robots_txt_notes",
    "language",
    "file_format",
    "text_layer_or_scanned",
    "page_count",
    "has_award_date",
    "has_successful_bidder_name",
    "has_contract_value",
    "has_number_of_bids",
    "has_loa_date",
    "has_pbg_percentage_or_deadline",
    "has_contact_details",
    "notes",
)
# The only columns the kit ever writes, and only where the cell is blank.
PREFILLED: Final = ("file_format", "text_layer_or_scanned", "page_count", "language")
FIELD_COLUMNS: Final = tuple(c for c in COLUMNS if c.startswith("has_"))
_BOM: Final = "\N{ZERO WIDTH NO-BREAK SPACE}"  # the UTF-8 byte-order mark Excel writes


class RecordsError(Exception):
    """records.csv cannot be processed safely; nothing was written."""


@dataclass(slots=True)
class Records:
    header: list[str]
    rows: list[dict[str, str]]
    bom: bool = False
    line_ending: str = "\n"
    added_columns: list[str] = field(default_factory=list)
    existed: bool = True  # False when there was no records.csv yet


def template_csv() -> str:
    return ",".join(COLUMNS) + "\n"


def read_records(path: Path) -> Records:
    """Load records.csv (a missing file gives an empty table with the template header)."""
    if not path.exists():
        return Records(header=list(COLUMNS), rows=[], existed=False)
    raw = path.read_bytes().decode("utf-8")  # UnicodeDecodeError: let the caller report it
    bom = raw.startswith(_BOM)
    text = raw.removeprefix(_BOM)
    line_ending = "\r\n" if "\r\n" in text.split("\n", 1)[0] + "\n" else "\n"
    reader = csv.reader(io.StringIO(text, newline=""))
    rows_raw = list(reader)
    if not rows_raw:
        return Records(header=list(COLUMNS), rows=[], bom=bom, line_ending=line_ending)
    header = [name.strip() for name in rows_raw[0]]
    if len(set(header)) != len(header) or "" in header:
        raise RecordsError(f"{path.name}: the header row has blank or repeated column names")
    if "file" not in header:
        raise RecordsError(f"{path.name}: the 'file' column is missing from the header row")
    rows = []
    for number, cells in enumerate(rows_raw[1:], start=2):
        if len(cells) > len(header):
            raise RecordsError(
                f"{path.name} line {number}: {len(cells)} cells but only {len(header)} "
                "columns; fix the row (a stray comma?) and run again"
            )
        if not any(cell.strip() for cell in cells):
            continue  # a blank line
        rows.append(dict(zip(header, cells + [""] * (len(header) - len(cells)), strict=True)))
    added = [c for c in COLUMNS if c not in header]
    for row in rows:
        row.update(dict.fromkeys(added, ""))
    return Records(header + added, rows, bom, line_ending, added)


def write_records(path: Path, records: Records) -> None:
    """Write atomically (a crash leaves the old file intact)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator=records.line_ending)
    writer.writerow(records.header)
    for row in records.rows:
        writer.writerow([row.get(column, "") for column in records.header])
    content = (_BOM if records.bom else "") + buffer.getvalue()
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".records-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def fill_blanks(row: dict[str, str], values: dict[str, str]) -> list[str]:
    """Set ``values`` only in blank cells of ``row``; return the columns filled."""
    filled = []
    for column, value in values.items():
        if value and not row.get(column, "").strip():
            row[column] = value
            filled.append(column)
    return filled
