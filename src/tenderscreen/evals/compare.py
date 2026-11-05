"""Compare predicted answers with labels, field by field, and count the outcomes."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from tenderscreen.evals.answers import Answer, DocumentAnswers, Found, ParsedFile


class Outcome(StrEnum):
    CORRECT = "correct"  # same value, or both not_found
    WRONG = "wrong"  # both have a value, and the values differ
    MISSED = "missed"  # the label has a value; the prediction said not_found
    INVENTED = "invented"  # the label says not_found; the prediction gave a value
    INVALID = "invalid"  # the prediction is missing or malformed


@dataclass(frozen=True, slots=True)
class FieldResult:
    doc_id: str
    field: str
    outcome: Outcome
    label: Answer
    prediction: Answer | None  # None when missing or malformed
    detail: str = ""  # why a prediction is invalid
    same_page: bool | None = None  # for correct values: same source_page as the label?


def judge(label: Answer, prediction: Answer) -> Outcome:
    """Outcome of one well-formed prediction against its label."""
    if isinstance(label, Found):
        if not isinstance(prediction, Found):
            return Outcome.MISSED
        return Outcome.CORRECT if prediction.value == label.value else Outcome.WRONG
    return Outcome.INVENTED if isinstance(prediction, Found) else Outcome.CORRECT


def compare_document(labels: DocumentAnswers, predicted: ParsedFile | None) -> list[FieldResult]:
    """One result per labelled field; a missing or malformed prediction is INVALID."""
    results = []
    for field, label in labels.fields.items():
        prediction = predicted.answers.get(field) if predicted else None
        if prediction is None:
            detail = "no prediction file"
            if predicted is not None:
                detail = predicted.field_errors.get(field) or "; ".join(predicted.problems)
                detail = detail or "field missing from prediction"
            results.append(FieldResult(labels.doc_id, field, Outcome.INVALID, label, None, detail))
            continue
        outcome = judge(label, prediction)
        same_page = None
        if (
            isinstance(label, Found)
            and isinstance(prediction, Found)
            and outcome is Outcome.CORRECT
        ):
            same_page = prediction.source_page == label.source_page
        results.append(
            FieldResult(labels.doc_id, field, outcome, label, prediction, same_page=same_page)
        )
    return results


@dataclass(slots=True)
class FieldStats:
    total: int = 0
    correct: int = 0
    wrong: int = 0
    missed: int = 0
    invented: int = 0
    invalid: int = 0
    pages_checked: int = 0
    pages_matched: int = 0

    def add(self, result: FieldResult) -> None:
        self.total += 1
        match result.outcome:
            case Outcome.CORRECT:
                self.correct += 1
            case Outcome.WRONG:
                self.wrong += 1
            case Outcome.MISSED:
                self.missed += 1
            case Outcome.INVENTED:
                self.invented += 1
            case Outcome.INVALID:
                self.invalid += 1
        if result.same_page is not None:
            self.pages_checked += 1
            self.pages_matched += result.same_page

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.total if self.total else None

    @property
    def page_agreement(self) -> float | None:
        return self.pages_matched / self.pages_checked if self.pages_checked else None


def tally(results: Iterable[FieldResult]) -> tuple[dict[str, FieldStats], FieldStats]:
    """Per-field statistics (sorted by field name) and the overall statistics."""
    per_field: dict[str, FieldStats] = {}
    overall = FieldStats()
    for result in results:
        per_field.setdefault(result.field, FieldStats()).add(result)
        overall.add(result)
    return dict(sorted(per_field.items())), overall
