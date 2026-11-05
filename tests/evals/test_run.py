"""Acceptance tests: perfect, wrong, invented and missed fields (Task E)."""

import json
import runpy
import sys
from datetime import datetime
from pathlib import Path

import pytest

from tenderscreen.evals import Outcome, evaluate
from tenderscreen.evals.run import LabelsError, main, write_outputs
from tenderscreen.ist import IST
from tests.evals.dataset import LABELS, NOT_FOUND, answer, write_folder

WHEN = datetime(2026, 11, 5, 6, 0, tzinfo=IST)


@pytest.fixture
def labels_dir(tmp_path: Path) -> Path:
    return write_folder(tmp_path / "labels", LABELS)


def predictions_with(**changes: dict[str, object]) -> dict[str, dict[str, object]]:
    predictions = {doc: dict(fields) for doc, fields in LABELS.items()}
    for doc, fields in changes.items():
        predictions[doc].update(fields)
    return predictions


def test_perfect_predictions_score_100_percent(labels_dir: Path, tmp_path: Path) -> None:
    # Same answers written differently: money in another format, other spacing and case.
    predictions = predictions_with(
        award_a={
            "contract_value_paise": answer(250_000_000, page=2),
            "successful_bidder_name": answer("  synthetic   BUILDERS pvt ltd"),
            "award_date": answer("2026-10-15"),
        },
        award_b={"contract_value_paise": answer("INR 2,50,00,000", page=3)},
    )
    evaluation = evaluate(labels_dir, write_folder(tmp_path / "pred", predictions))
    assert evaluation.overall.total == 12
    assert evaluation.overall.accuracy == 1.0
    assert evaluation.overall.page_agreement == 1.0
    assert {r.outcome for r in evaluation.results} == {Outcome.CORRECT}


def test_wrong_invented_and_missed_are_counted_per_field(labels_dir: Path, tmp_path: Path) -> None:
    predictions = predictions_with(
        award_a={
            "contract_value_paise": answer("Rs. 25,00,001", page=2),  # wrong by one rupee
            "award_date": answer("16/10/2026"),  # wrong day
            "number_of_bids": NOT_FOUND,  # missed
            "pbg_percentage": answer("10"),  # invented
        },
        award_b={"award_date": answer("01/01/2027")},  # invented
    )
    evaluation = evaluate(labels_dir, write_folder(tmp_path / "pred", predictions))
    stats = evaluation.per_field
    assert (stats["contract_value_paise"].wrong, stats["contract_value_paise"].accuracy) == (1, 0.5)
    assert (stats["award_date"].wrong, stats["award_date"].invented) == (1, 1)
    assert stats["award_date"].accuracy == 0.0
    assert (stats["number_of_bids"].missed, stats["number_of_bids"].accuracy) == (1, 0.5)
    assert (stats["pbg_percentage"].invented, stats["pbg_percentage"].accuracy) == (1, 0.5)
    assert stats["successful_bidder_name"].accuracy == 1.0
    overall = evaluation.overall
    assert (overall.correct, overall.wrong, overall.missed, overall.invented) == (7, 2, 1, 2)


def test_dates_compare_exactly_including_time(labels_dir: Path, tmp_path: Path) -> None:
    predictions = predictions_with(award_a={"award_date": answer("15/10/2026 00:00")})
    evaluation = evaluate(labels_dir, write_folder(tmp_path / "pred", predictions))
    assert evaluation.per_field["award_date"].wrong == 1  # a time the label does not state


def test_unpaired_files_and_fields_are_reported(labels_dir: Path, tmp_path: Path) -> None:
    predictions = predictions_with(award_a={"extra_field": answer("x")})
    del predictions["award_b"]
    predictions["award_c"] = dict(LABELS["award_a"])
    evaluation = evaluate(labels_dir, write_folder(tmp_path / "pred", predictions))
    inventory = evaluation.inventory
    assert inventory.without_prediction == ["award_b"]
    assert inventory.without_label == ["award_c"]
    assert inventory.unlabelled_fields == ["award_a: extra_field"]
    assert evaluation.overall.invalid == 6  # every award_b field


def test_unreadable_prediction_file_counts_as_invalid(labels_dir: Path, tmp_path: Path) -> None:
    pred = write_folder(tmp_path / "pred", {"award_a": LABELS["award_a"]})
    (pred / "award_b.json").write_text("{broken", encoding="utf-8")
    evaluation = evaluate(labels_dir, pred)
    assert evaluation.overall.invalid == 6
    details = {r.detail for r in evaluation.results if r.doc_id == "award_b"}
    assert len(details) == 1
    assert details.pop().startswith("not valid UTF-8 JSON")


def test_invalid_labels_stop_the_evaluation(tmp_path: Path) -> None:
    bad: dict[str, dict[str, object]] = {
        "award_a": {"award_date": answer("TODO")},
        "award_b": {"emd_paise": answer(1.5)},
    }
    labels = write_folder(tmp_path / "labels", bad)
    with pytest.raises(LabelsError) as caught:
        evaluate(labels, write_folder(tmp_path / "pred", {}))
    assert len(caught.value.problems) == 2
    assert caught.value.problems[0].startswith("award_a.json: award_date: still says TODO")


def test_empty_labels_folder_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(LabelsError, match="no label files"):
        evaluate(write_folder(tmp_path / "l", {}), write_folder(tmp_path / "p", {}))


def test_outputs_are_written(labels_dir: Path, tmp_path: Path) -> None:
    predictions = predictions_with(award_a={"number_of_bids": NOT_FOUND})
    evaluation = evaluate(labels_dir, write_folder(tmp_path / "pred", predictions))
    out = tmp_path / "out"
    markdown = write_outputs(evaluation, out, WHEN)
    assert (out / "report.md").read_text(encoding="utf-8") == markdown
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["fields"]["number_of_bids"]["missed"] == 1
    assert summary["overall"]["accuracy"] == round(11 / 12, 4)
    assert summary["generated_at"] == "2026-11-05T06:00:00+05:30"


def test_cli_success(labels_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pred = write_folder(tmp_path / "pred", LABELS)
    out = tmp_path / "out"
    assert main([str(labels_dir), str(pred), "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert "# Extraction evaluation" in captured.out
    assert "None: every field matched its label." in captured.out
    assert "summary.json" in captured.err
    assert (out / "summary.json").is_file()


def test_cli_bad_labels(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    labels = write_folder(tmp_path / "labels", {"x": {"award_date": answer("31/02/2026")}})
    assert main([str(labels), str(write_folder(tmp_path / "p", {}))]) == 2
    assert "fix these label problems first" in capsys.readouterr().err


def test_cli_missing_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(tmp_path / "nope"), str(tmp_path)]) == 2
    assert "is not a folder" in capsys.readouterr().err


def test_module_entry_point(labels_dir: Path, tmp_path: Path) -> None:
    import subprocess

    pred = write_folder(tmp_path / "pred", LABELS)
    out = tmp_path / "out"
    result = subprocess.run(  # noqa: S603 - fixed command, test-created folders
        [sys.executable, "-m", "tenderscreen.evals", str(labels_dir), str(pred), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (out / "report.md").is_file()


@pytest.mark.filterwarnings("ignore:.*found in sys.modules:RuntimeWarning")
def test_main_module_in_process(
    labels_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pred = write_folder(tmp_path / "pred", LABELS)
    argv = ["tenderscreen.evals", str(labels_dir), str(pred), "--out", str(tmp_path / "out")]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("tenderscreen.evals", run_name="__main__")
    assert exc.value.code == 0
