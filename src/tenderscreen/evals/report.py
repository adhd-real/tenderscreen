"""Render evaluation results as a Markdown report and a JSON summary."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from tenderscreen.evals.answers import Answer, Found
from tenderscreen.evals.compare import FieldResult, FieldStats, Outcome
from tenderscreen.evals.fields import FieldKind, field_kind, is_private_field

_MAX_SHOWN: Final = 60
_MAX_NAME: Final = 80

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
    # "doc_id: label names X, prediction names Y": probably paired with the wrong label
    document_mismatches: list[str] = field(default_factory=list)


def plain(text: str, limit: int = _MAX_SHOWN) -> str:
    """Text made safe for one Markdown table cell or list item, and shortened."""
    flat = " ".join(text.split()).replace("|", "/").replace("`", "'")
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def code(text: str) -> str:
    """A name (field, document) as inline code that cannot break the Markdown."""
    return f"`{plain(text, _MAX_NAME)}`"


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
    if is_private_field(field):
        return "(hidden: contact data)"
    if field_kind(field) is FieldKind.MONEY and isinstance(answer.value, int):
        return f"{format_paise(answer.value)} (p. {answer.source_page})"
    return f"{plain(str(answer.raw_value))} (p. {answer.source_page})"


def show_detail(field: str, detail: str) -> str:
    """Why a prediction is invalid or unreadable; never contact data, never long."""
    return "(details hidden: contact data)" if is_private_field(field) else plain(detail, 120)


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
    lines += [_stats_row(code(name), field_kind(name), s) for name, s in per_field.items()]
    lines += [_stats_row("**All fields**", "", overall), "", EXPLANATION]

    problems = [
        *(f"No prediction for labelled document {code(d)}" for d in inventory.without_prediction),
        *(f"Prediction without labels (ignored): {code(d)}" for d in inventory.without_label),
        *(
            f"Predicted field not in labels (ignored): {code(f)}"
            for f in inventory.unlabelled_fields
        ),
        *(
            f"Document name differs (check the pairing): {plain(m, 200)}"
            for m in inventory.document_mismatches
        ),
    ]
    if problems:
        lines += ["## Problems", "", *(f"- {p}" for p in problems), ""]

    mistakes = [r for r in results if r.outcome is not Outcome.CORRECT]
    lines += ["## Mistakes by document", ""]
    if not mistakes:
        lines += ["None: every field matched its label.", ""]
    for doc_id in sorted({r.doc_id for r in mistakes}):
        lines += [
            f"### {code(doc_id)}",
            "",
            "| Field | Outcome | Label | Prediction |",
            "|---|---|---|---|",
        ]
        for r in (m for m in mistakes if m.doc_id == doc_id):
            predicted = show(r.field, r.prediction)
            if r.outcome is Outcome.INVALID:
                predicted = f"invalid: {show_detail(r.field, r.detail)}"
            elif r.prediction is None:  # a value the prediction claims but that is unreadable
                predicted = show_detail(r.field, r.detail)
            label = show(r.field, r.label)
            lines.append(f"| {code(r.field)} | {r.outcome} | {label} | {predicted} |")
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
        "document_mismatches": len(inventory.document_mismatches),
        "fields": {
            name: {"kind": str(field_kind(name)), **_stats_json(s)} for name, s in per_field.items()
        },
        "overall": _stats_json(overall),
        "results": [
            {"document": r.doc_id, "field": r.field, "outcome": str(r.outcome)} for r in results
        ],
    }
