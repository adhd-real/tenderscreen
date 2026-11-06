"""``data/manual/records.csv``: read it, fill only blank cells, write it back safely.

The kit never overwrites a non-empty cell, never reorders or drops rows, keeps
any extra columns you added, and keeps the file's encoding mark (BOM), line
endings and permissions. If the CSV is malformed (duplicate headers, a row with
more cells than the header, broken quoting) it stops instead of guessing, so
nothing you typed can be lost.
"""

import csv
import io
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from tenderscreen.store.documents import sync_directory

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
    # The file's bytes as read, so that edits saved while the kit runs (OCR can
    # take minutes) are never overwritten. None: not read from a file.
    source: bytes | None = None


def template_csv() -> str:
    return ",".join(COLUMNS) + "\n"


def _parse_csv(name: str, text: str) -> list[tuple[int, list[str]]]:
    """Rows with the line each ends on; broken quoting is an error, never a guess.

    Without ``strict``, a stray double quote silently swallows every following
    line into one cell, and writing the file back would merge those rows.
    """
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    rows = []
    try:
        for cells in reader:
            rows.append((reader.line_num, cells))
    except csv.Error as exc:
        raise RecordsError(
            f"{name}: broken CSV at or before line {reader.line_num} ({exc}); "
            "look for a stray double quote and run again"
        ) from exc
    return rows


def _swallowed_rows(cell: str, columns: int) -> bool:
    """True if a cell runs over lines that look like rows of their own.

    A stray double quote at the start of a cell, closed by another stray quote
    on a later row (an inch mark: 6"), still makes valid CSV: every row in
    between ends up inside one cell. A note that spans lines rarely has half as
    many commas on one line as the table has columns; a swallowed row does.
    """
    later_lines = cell.splitlines()[1:]
    return any(line.count(",") >= max(1, columns // 2) for line in later_lines)


def read_records(path: Path) -> Records:
    """Load records.csv (a missing file gives an empty table with the template header)."""
    if not path.exists():
        return Records(header=list(COLUMNS), rows=[], existed=False)
    source = path.read_bytes()
    raw = source.decode("utf-8")  # UnicodeDecodeError: let the caller report it
    bom = raw.startswith(_BOM)
    text = raw.removeprefix(_BOM)
    line_ending = "\r\n" if "\r\n" in text.split("\n", 1)[0] + "\n" else "\n"
    rows_raw = _parse_csv(path.name, text)
    if not rows_raw:
        return Records(list(COLUMNS), [], bom=bom, line_ending=line_ending, source=source)
    width = len(rows_raw[0][1])  # cells in the header row, including blank ones
    header = [name.strip() for name in rows_raw[0][1]]
    while header and not header[-1]:  # Excel can add empty columns at the end
        header.pop()
    if len(set(header)) != len(header) or "" in header:
        raise RecordsError(f"{path.name}: the header row has blank or repeated column names")
    if "file" not in header:
        raise RecordsError(f"{path.name}: the 'file' column is missing from the header row")
    rows = []
    for line, cells in rows_raw[1:]:
        if len(cells) > width:
            raise RecordsError(
                f"{path.name} line {line}: {len(cells)} cells but only {width} "
                "columns; fix the row (a stray comma?) and run again"
            )
        if any(cell.strip() for cell in cells[len(header) :]):
            raise RecordsError(
                f"{path.name} line {line}: a value in a column with no name; "
                "name the column in the header row and run again"
            )
        cells = cells[: len(header)]
        if any(_swallowed_rows(cell, width) for cell in cells):
            first = line - sum(cell.count("\n") for cell in cells)
            raise RecordsError(
                f"{path.name} line {first}: a cell runs over the following lines as if they "
                "were part of it; look for a stray double quote and run again"
            )
        if not any(cell.strip() for cell in cells):
            continue  # a blank line
        rows.append(dict(zip(header, cells + [""] * (len(header) - len(cells)), strict=True)))
    added = [c for c in COLUMNS if c not in header]
    for row in rows:
        row.update(dict.fromkeys(added, ""))
    return Records(header + added, rows, bom, line_ending, added, source=source)


def _default_file_mode() -> int:
    """The mode a newly created file gets: 0o666 minus the process umask.

    Reading the umask means setting it; the kit is single-threaded here.
    """
    umask = os.umask(0o022)
    os.umask(umask)
    return 0o666 & ~umask


def _file_mode(path: Path) -> int:
    """Permission bits to give the new version of ``path``: the old file's, if any."""
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return _default_file_mode()


def _check_unchanged(path: Path, records: Records) -> None:
    """Refuse to replace a file that was edited, created or removed since it was read."""
    if not records.existed:
        if path.exists():
            raise RecordsError(
                f"{path.name} was created while the kit was running; run the kit again"
            )
        return
    if records.source is None:
        return  # not read from a file: nothing to compare with
    try:
        current: bytes | None = path.read_bytes()
    except FileNotFoundError:
        current = None
    if current != records.source:
        raise RecordsError(
            f"{path.name} was changed while the kit was running; your changes are kept, "
            "run the kit again"
        )


def write_records(path: Path, records: Records) -> None:
    """Write atomically and durably: a crash leaves the old file or the new one, whole.

    The file keeps its permissions (a temporary file starts as owner-only), and
    it is not replaced if it changed after ``read_records`` (``RecordsError``).
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator=records.line_ending)
    writer.writerow(records.header)
    for row in records.rows:
        writer.writerow([row.get(column, "") for column in records.header])
    content = (_BOM if records.bom else "") + buffer.getvalue()
    mode = _file_mode(path)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".records-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            os.fchmod(handle.fileno(), mode)  # before the fsync, so the mode is durable too
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _check_unchanged(path, records)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    sync_directory(path.parent)


def fill_blanks(row: dict[str, str], values: dict[str, str]) -> list[str]:
    """Set ``values`` only in blank cells of ``row``; return the columns filled."""
    filled = []
    for column, value in values.items():
        if value and not row.get(column, "").strip():
            row[column] = value
            filled.append(column)
    return filled
