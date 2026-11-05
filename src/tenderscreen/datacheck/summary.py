"""Count how many records contain each field, and other useful breakdowns.

The summary has no generation time on purpose: re-running the kit on unchanged
records then leaves records-summary.md unchanged, so git shows no noise.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Final

from tenderscreen.datacheck.records import FIELD_COLUMNS

_YES: Final = frozenset({"y", "yes", "true", "1", "हाँ", "हां"})
_NO: Final = frozenset({"n", "no", "false", "0", "नहीं"})
NOTICE_TYPES: Final = ("award", "tender")
BREAKDOWN_COLUMNS: Final = (
    "portal",
    "notice_type",
    "captcha_needed",
    "language",
    "file_format",
    "text_layer_or_scanned",
)


def cell(text: str) -> str:
    """Typed text made safe for one Markdown table cell or list item."""
    return " ".join(text.split()).replace("|", "/")


def code(text: str) -> str:
    """A file name as inline code that cannot break the Markdown."""
    return "`" + cell(text).replace("`", "'") + "`"


def _value(row: dict[str, str], column: str) -> str:
    return cell(row.get(column, "")).casefold() or "(blank)"


def yes_no(text: str) -> str:
    """ "yes", "no", "blank" or "unclear" for a y/n cell."""
    value = text.strip().casefold()
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
    return Counter(_value(row, column) for row in rows)


def by_portal(rows: Sequence[dict[str, str]]) -> list[str]:
    """Markdown tables: records per portal and notice type, and fields present per portal."""
    portals = sorted({_value(row, "portal") for row in rows})
    if not portals:
        return []
    types = [*NOTICE_TYPES, "other", "(blank)"]
    lines = [
        "## Records by portal and notice type",
        "",
        "| Portal | " + " | ".join(types) + " | Total |",
        "|---|" + "---:|" * (len(types) + 1),
    ]
    fields = [column.removeprefix("has_").replace("_", " ") for column in FIELD_COLUMNS]
    present = [
        "## Fields present, by portal (records saying y)",
        "",
        "| Portal | Records | " + " | ".join(fields) + " |",
        "|---|" + "---:|" * (len(fields) + 1),
    ]
    for portal in portals:
        mine = [row for row in rows if _value(row, "portal") == portal]
        kinds = Counter(
            kind if kind in NOTICE_TYPES or kind == "(blank)" else "other"
            for kind in (_value(row, "notice_type") for row in mine)
        )
        lines.append(
            f"| {portal} | " + " | ".join(str(kinds[t]) for t in types) + f" | {len(mine)} |"
        )
        counts = field_counts(mine)
        present.append(
            f"| {portal} | {len(mine)} | "
            + " | ".join(str(counts[column]["yes"]) for column in FIELD_COLUMNS)
            + " |"
        )
    return [*lines, "", *present, ""]


def render_summary(rows: Sequence[dict[str, str]], problems: Sequence[str]) -> str:
    """Markdown summary of the records table."""
    total = len(rows)
    lines = [
        "# Manual data check: summary",
        "",
        f"From `records.csv`: **{total} records**.",
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
    lines += by_portal(rows)
    lines += ["## Other breakdowns", ""]
    for column in BREAKDOWN_COLUMNS:
        counts = breakdown(rows, column)
        if not counts:
            continue
        items = ", ".join(f"{value}: {n}" for value, n in sorted(counts.items()))
        lines.append(f"- **{column.replace('_', ' ')}**: {items}")
    if problems:
        lines += ["", "## Problems", "", *(f"- {p}" for p in problems)]
    return "\n".join(lines) + "\n"
