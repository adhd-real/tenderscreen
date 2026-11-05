"""Evaluate a folder of predictions against a folder of labels.

    python -m tenderscreen.evals data/labels data/predictions [--out eval-results]

Writes ``report.md`` and ``summary.json`` into ``--out`` and prints the report.
Exit status: 0 when the evaluation ran (whatever the scores), 2 when the labels
are invalid or a folder is missing, since scores against bad labels mean nothing.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tenderscreen.evals.answers import (
    AnswerFileError,
    ParsedFile,
    answer_files,
    load_answers,
    parse_file,
    template_fields,
)
from tenderscreen.evals.compare import FieldResult, FieldStats, compare_document, tally
from tenderscreen.evals.report import Inventory, render_markdown, summary_json
from tenderscreen.ist import now_ist


class LabelsError(Exception):
    """One or more label files are invalid; ``problems`` lists them all."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class Evaluation:
    results: list[FieldResult]
    per_field: dict[str, FieldStats]
    overall: FieldStats
    inventory: Inventory


def evaluate(labels_dir: Path, predictions_dir: Path) -> Evaluation:
    """Compare every labelled document with its prediction (matched by file name)."""
    label_paths = answer_files(labels_dir)
    prediction_paths = answer_files(predictions_dir)
    if not label_paths:
        raise LabelsError([f"no label files (*.json) in {labels_dir}"])
    try:
        known_fields = template_fields(labels_dir)
    except AnswerFileError as exc:
        raise LabelsError([f"_template.json: {problem}" for problem in exc.problems]) from exc

    labels, problems = [], []
    for path in label_paths.values():
        try:
            labels.append(load_answers(path, known_fields=known_fields))
        except AnswerFileError as exc:
            problems += [f"{path.name}: {problem}" for problem in exc.problems]
    if problems:
        raise LabelsError(problems)

    results: list[FieldResult] = []
    unlabelled_fields: list[str] = []
    mismatches: list[str] = []
    for doc in labels:
        prediction_path = prediction_paths.get(doc.doc_id)
        predicted = None
        if prediction_path is not None:
            try:
                predicted = parse_file(prediction_path)
            except AnswerFileError as exc:  # unreadable JSON: every field is invalid
                predicted = ParsedFile(doc.doc_id, "", {}, {}, exc.problems)
            else:
                extra = sorted(
                    (set(predicted.answers) | set(predicted.field_errors)) - set(doc.fields)
                )
                unlabelled_fields += [f"{doc.doc_id}: {name}" for name in extra]
                if predicted.document and predicted.document != doc.document:
                    mismatches.append(
                        f"{doc.doc_id}: label names {doc.document}, "
                        f"prediction names {predicted.document}"
                    )
        results += compare_document(doc, predicted)

    per_field, overall = tally(results)
    inventory = Inventory(
        labels_dir=str(labels_dir),
        predictions_dir=str(predictions_dir),
        labelled=len(label_paths),
        predicted=len(prediction_paths),
        without_prediction=sorted(set(label_paths) - set(prediction_paths)),
        without_label=sorted(set(prediction_paths) - set(label_paths)),
        unlabelled_fields=unlabelled_fields,
        document_mismatches=mismatches,
    )
    return Evaluation(results, per_field, overall, inventory)


def printable(text: str) -> str:
    """Text that can always be written as UTF-8.

    Python keeps undecodable bytes in file names as lone surrogates, which cannot
    be encoded; they become visible escapes (``\\udcff``) instead of a crash.
    """
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def write_outputs(evaluation: Evaluation, out_dir: Path, generated_at: datetime) -> str:
    """Write report.md and summary.json; return the Markdown."""
    args = (evaluation.results, evaluation.per_field, evaluation.overall, evaluation.inventory)
    markdown = printable(render_markdown(*args, generated_at))
    summary = printable(json.dumps(summary_json(*args, generated_at), indent=2, ensure_ascii=False))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(markdown, encoding="utf-8")
    (out_dir / "summary.json").write_text(summary + "\n", encoding="utf-8")
    return markdown


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tenderscreen.evals",
        description="Compare predicted extraction results with hand-labelled answers.",
    )
    parser.add_argument("labels_dir", type=Path, help="folder of labelled <document>.json files")
    parser.add_argument("predictions_dir", type=Path, help="folder of predicted <document>.json")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("eval-results"),
        help="folder for report.md and summary.json (default: eval-results)",
    )
    args = parser.parse_args(argv)
    try:
        evaluation = evaluate(args.labels_dir, args.predictions_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except LabelsError as exc:
        print("error: fix these label problems first:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    try:
        markdown = write_outputs(evaluation, args.out, now_ist())
    except OSError as exc:
        print(f"error: cannot write the report to {args.out}: {exc}", file=sys.stderr)
        return 2
    print(markdown)
    print(f"\nWrote {args.out / 'report.md'} and {args.out / 'summary.json'}", file=sys.stderr)
    return 0
