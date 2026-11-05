"""Render evaluation results as a Markdown report and a JSON summary."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from tenderscreen.evals.answers import Answer, Found
from tenderscreen.evals.compare import FieldResult, FieldStats, Outcome
from tenderscreen.evals.fields import FieldKind, field_kind

# Fields whose values may hold personal contact data; reports never show them
# (CLAUDE.md: never log phone numbers).
_PRIVATE_FIELD: Final = re.compile(r"contact|phone|mobile|email|e_mail|address", re.IGNORECASE)
_MAX_SHOWN: Final = 60

EXPLANATION: Final = (
    "- **Missed**: the label has an answer, but the prediction said not_found.\n"
    "- **Invented**: the label says not_found, but the prediction gave a value.\n"
    "- **Wrong**: both gave a value, and the values differ.\n"
    "- **Invalid**: the prediction file or field is missing or malformed.\n"
    "- **Page agreement**: of the correct values, how many cite the label's source page.\n"
    "- Money is compared exactly in paise, dates exactly, text ignoring spacing and case.\n"
)


@dataclass(frozen=True, slots=True)
class Inventory:
    """What was compared, and what could not be paired."""

    labels_dir: str
    predictions_dir: str
    labelled: int
    predicted: int
    without_prediction: list[str]
    without_label: list[str]
    unlabelled_fields: list[str]  # "doc_id: field" predicted but not labelled


def _percent(ratio: float | None) -> str:
    return "n/a" if ratio is None else f"{ratio:.0%}"


def _ratio(ratio: float | None) -> float | None:
    return None if ratio is None else round(ratio, 4)


def format_paise(paise: int) -> str:
    """Paise as rupees with Indian digit grouping, for people: 250000050 -> ₹ 25,00,000.50."""
    rupees, cents = divmod(paise, 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        digits = ",".join([head, *groups, tail])
    return f"\N{INDIAN RUPEE SIGN} {digits}.{cents:02d}"


def show(field: str, answer: Answer | None) -> str:
    """A short, Markdown-safe rendering of an answer; private fields are hidden."""
    if answer is None:
        return "(none)"
    if not isinstance(answer, Found):
        return "not_found"
    if _PRIVATE_FIELD.search(field):
        return "(hidden: contact data)"
    if field_kind(field) is FieldKind.MONEY and isinstance(answer.value, int):
        return f"{format_paise(answer.value)} (p. {answer.source_page})"
    text = " ".join(str(answer.raw_value).split())
    if len(text) > _MAX_SHOWN:
        text = text[: _MAX_SHOWN - 3] + "..."
    return f"{text.replace('|', '/')} (p. {answer.source_page})"


def _stats_row(name: str, kind: str, stats: FieldStats) -> str:
    cells = [
        name,
        kind,
        str(stats.total),
        str(stats.correct),
        _percent(stats.accuracy),
        str(stats.wrong),
        str(stats.missed),
        str(stats.invented),
        str(stats.invalid),
        _percent(stats.page_agreement),
    ]
    return "| " + " | ".join(cells) + " |"


def render_markdown(
    results: list[FieldResult],
    per_field: dict[str, FieldStats],
    overall: FieldStats,
    inventory: Inventory,
    generated_at: datetime,
) -> str:
    lines = [
        "# Extraction evaluation",
        "",
        f"- Labels: `{inventory.labels_dir}` ({inventory.labelled} documents)",
        f"- Predictions: `{inventory.predictions_dir}` ({inventory.predicted} files)",
        f"- Generated: {generated_at.isoformat(timespec='seconds')}",
        "",
        "## Per field",
        "",
        "| Field | Kind | Labelled | Correct | Accuracy | Wrong | Missed | Invented"
        " | Invalid | Page agreement |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines += [_stats_row(f"`{name}`", field_kind(name), s) for name, s in per_field.items()]
    lines += [_stats_row("**All fields**", "", overall), "", EXPLANATION]

    problems = [
        *(f"No prediction for labelled document `{d}`" for d in inventory.without_prediction),
        *(f"Prediction without labels (ignored): `{d}`" for d in inventory.without_label),
        *(f"Predicted field not in labels (ignored): `{f}`" for f in inventory.unlabelled_fields),
    ]
    if problems:
        lines += ["## Problems", "", *(f"- {p}" for p in problems), ""]

    mistakes = [r for r in results if r.outcome is not Outcome.CORRECT]
    lines += ["## Mistakes by document", ""]
    if not mistakes:
        lines += ["None: every field matched its label.", ""]
    for doc_id in sorted({r.doc_id for r in mistakes}):
        lines += [
            f"### `{doc_id}`",
            "",
            "| Field | Outcome | Label | Prediction |",
            "|---|---|---|---|",
        ]
        for r in (m for m in mistakes if m.doc_id == doc_id):
            predicted = show(r.field, r.prediction)
            if r.outcome is Outcome.INVALID:
                predicted = f"invalid: {r.detail.replace('|', '/')}"
            lines.append(f"| `{r.field}` | {r.outcome} | {show(r.field, r.label)} | {predicted} |")
        lines.append("")
    return "\n".join(lines)


def _stats_json(stats: FieldStats) -> dict[str, object]:
    return {
        "labelled": stats.total,
        "correct": stats.correct,
        "accuracy": _ratio(stats.accuracy),
        "wrong": stats.wrong,
        "missed": stats.missed,
        "invented": stats.invented,
        "invalid": stats.invalid,
        "page_agreement": _ratio(stats.page_agreement),
    }


def summary_json(
    results: list[FieldResult],
    per_field: dict[str, FieldStats],
    overall: FieldStats,
    inventory: Inventory,
    generated_at: datetime,
) -> dict[str, object]:
    """Counts and outcomes only: no field values, so it is safe to share."""
    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "labels_dir": inventory.labels_dir,
        "predictions_dir": inventory.predictions_dir,
        "documents": {
            "labelled": inventory.labelled,
            "predicted": inventory.predicted,
            "without_prediction": inventory.without_prediction,
            "without_label": inventory.without_label,
        },
        "unlabelled_fields": inventory.unlabelled_fields,
        "fields": {
            name: {"kind": str(field_kind(name)), **_stats_json(s)} for name, s in per_field.items()
        },
        "overall": _stats_json(overall),
        "results": [
            {"document": r.doc_id, "field": r.field, "outcome": str(r.outcome)} for r in results
        ],
    }
