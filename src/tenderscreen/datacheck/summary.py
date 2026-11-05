"""Count how many records contain each field, and other useful breakdowns."""

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Final

from tenderscreen.datacheck.records import FIELD_COLUMNS

_YES: Final = frozenset({"y", "yes", "true", "1", "हाँ", "हां"})
_NO: Final = frozenset({"n", "no", "false", "0", "नहीं"})
BREAKDOWN_COLUMNS: Final = (
    "portal",
    "notice_type",
    "captcha_needed",
    "language",
    "file_format",
    "text_layer_or_scanned",
)


def yes_no(cell: str) -> str:
    """ "yes", "no", "blank" or "unclear" for a y/n cell."""
    value = cell.strip().casefold()
    if not value:
        return "blank"
    if value in _YES:
        return "yes"
    if value in _NO:
        return "no"
    return "unclear"


def field_counts(rows: Sequence[dict[str, str]]) -> dict[str, Counter[str]]:
    return {
        column: Counter(yes_no(row.get(column, "")) for row in rows) for column in FIELD_COLUMNS
    }


def breakdown(rows: Iterable[dict[str, str]], column: str) -> Counter[str]:
    return Counter(row.get(column, "").strip().casefold() or "(blank)" for row in rows)


def render_summary(
    rows: Sequence[dict[str, str]], problems: Sequence[str], generated_at: str
) -> str:
    """Markdown summary of the records table."""
    total = len(rows)
    lines = [
        "# Manual data check: summary",
        "",
        f"Generated {generated_at} from `records.csv`: **{total} records**.",
        "",
        "## How many records contain each field",
        "",
        "| Field | Yes | No | Blank | Unclear | Yes share |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for column, counts in field_counts(rows).items():
        share = f"{counts['yes'] / total:.0%}" if total else "n/a"
        name = column.removeprefix("has_").replace("_", " ")
        lines.append(
            f"| {name} | {counts['yes']} | {counts['no']} | {counts['blank']} "
            f"| {counts['unclear']} | {share} |"
        )
    lines += ["", "Write `y` or `n` in the has_... columns; anything else counts as unclear.", ""]
    for column in BREAKDOWN_COLUMNS:
        counts = breakdown(rows, column)
        if not counts:
            continue
        items = ", ".join(f"{value}: {n}" for value, n in sorted(counts.items()))
        lines.append(f"- **{column.replace('_', ' ')}**: {items}")
    if problems:
        lines += ["", "## Problems", "", *(f"- {p}" for p in problems)]
    return "\n".join(lines) + "\n"
