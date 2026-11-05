"""Scan data/manual/, pre-fill records.csv (blank cells only), and summarise it.

    python -m tenderscreen.datacheck [--manual-dir data/manual] [--no-ocr]

For every document file in the folder it makes sure records.csv has a row (by
the ``file`` column), then fills only *blank* cells of: file_format,
text_layer_or_scanned, page_count, language. Anything you typed is kept. It
writes records-summary.md next to records.csv and prints it. OCR results are
cached in data/raw/ (git-ignored), so re-running is fast.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from tenderscreen.datacheck.inspect import FileFacts, inspect_file
from tenderscreen.datacheck.records import (
    PREFILLED,
    Records,
    RecordsError,
    fill_blanks,
    read_records,
    write_records,
)
from tenderscreen.datacheck.summary import render_summary
from tenderscreen.documents import ExtractionLimits
from tenderscreen.ist import now_ist
from tenderscreen.store import DEFAULT_ROOT, ResultCache, open_store

RECORDS_NAME: Final = "records.csv"
SUMMARY_NAME: Final = "records-summary.md"
NOT_DOCUMENTS: Final = frozenset({RECORDS_NAME, SUMMARY_NAME, "README.md"})


@dataclass(slots=True)
class CheckResult:
    records: Records
    new_rows: int = 0
    filled_cells: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        records = self.records
        return bool(
            self.new_rows or self.filled_cells or records.added_columns or not records.existed
        )


def list_documents(manual_dir: Path) -> list[str]:
    """Document files under ``manual_dir`` as POSIX paths relative to it, sorted."""
    found = []
    for path in sorted(manual_dir.rglob("*")):
        relative = path.relative_to(manual_dir)
        if not path.is_file() or any(part.startswith(".") for part in relative.parts):
            continue
        if len(relative.parts) == 1 and relative.name in NOT_DOCUMENTS:
            continue
        found.append(relative.as_posix())
    return found


def check(
    manual_dir: Path,
    *,
    ocr: bool = True,
    cache: ResultCache | None = None,
    limits: ExtractionLimits | None = None,
) -> CheckResult:
    """Pre-fill the records in memory; nothing is written here."""
    records = read_records(manual_dir / RECORDS_NAME)
    result = CheckResult(records)
    documents = list_documents(manual_dir)
    for name in documents:
        rows = [row for row in records.rows if row.get("file", "").strip() == name]
        if rows and all(row.get(column, "").strip() for row in rows for column in PREFILLED):
            continue  # every kit cell already filled: nothing to inspect
        facts: FileFacts = inspect_file(manual_dir / name, ocr=ocr, cache=cache, limits=limits)
        if facts.problem:
            result.problems.append(f"`{name}`: {facts.problem}")
        if not rows:
            row = dict.fromkeys(records.header, "")
            row["file"] = name
            records.rows.append(row)
            rows = [row]
            result.new_rows += 1
        for row in rows:
            result.filled_cells += len(fill_blanks(row, facts.prefill()))
    known = set(documents)
    for row in records.rows:
        name = row.get("file", "").strip()
        if name and name not in known:
            result.problems.append(f"records.csv names `{name}`, but there is no such file")
    return result


def run(manual_dir: Path, *, ocr: bool, store_root: Path) -> str:
    """Check, write records.csv if anything changed, write and return the summary."""
    if ocr:
        with open_store(store_root) as store:
            result = check(manual_dir, ocr=True, cache=store.cache)
    else:
        result = check(manual_dir, ocr=False)
    if result.changed:
        write_records(manual_dir / RECORDS_NAME, result.records)
    summary = render_summary(
        result.records.rows, result.problems, now_ist().isoformat(timespec="minutes")
    )
    (manual_dir / SUMMARY_NAME).write_text(summary, encoding="utf-8")
    changes = (
        f"{result.new_rows} new rows, {result.filled_cells} cells filled"
        if result.changed
        else "no changes"
    )
    return f"{summary}\nrecords.csv: {changes}."


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tenderscreen.datacheck",
        description="Pre-fill data/manual/records.csv from the files in data/manual/ "
        "(blank cells only) and summarise which fields the records contain.",
    )
    parser.add_argument("--manual-dir", type=Path, default=Path("data/manual"))
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="do not OCR scanned PDFs (their language stays blank)",
    )
    parser.add_argument(
        "--store", type=Path, default=DEFAULT_ROOT, help="OCR cache folder (default data/raw)"
    )
    args = parser.parse_args(argv)
    if not args.manual_dir.is_dir():
        print(f"error: {args.manual_dir} is not a folder", file=sys.stderr)
        return 2
    try:
        print(run(args.manual_dir, ocr=not args.no_ocr, store_root=args.store))
    except RecordsError as exc:
        print(f"error: {exc}. records.csv was not changed.", file=sys.stderr)
        return 2
    except UnicodeDecodeError:
        print(
            "error: records.csv is not UTF-8 text. In Excel use Save As > "
            "'CSV UTF-8 (Comma delimited)'. records.csv was not changed.",
            file=sys.stderr,
        )
        return 2
    return 0
