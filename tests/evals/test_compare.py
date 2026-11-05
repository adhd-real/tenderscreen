from pathlib import Path

from tenderscreen.evals.answers import Found, NotFound, load_answers, parse_file
from tenderscreen.evals.compare import (
    FieldResult,
    FieldStats,
    Outcome,
    compare_document,
    judge,
    tally,
)
from tests.evals.dataset import LABELS, NOT_FOUND, answer, write_folder

VALUE = Found("Rs. 5", 500, 1, "q")
OTHER = Found("Rs. 6", 600, 1, "q")


def test_judge_all_outcomes() -> None:
    assert judge(VALUE, Found("₹ 5.00", 500, 4, "other quote")) is Outcome.CORRECT
    assert judge(NotFound(), NotFound()) is Outcome.CORRECT
    assert judge(VALUE, OTHER) is Outcome.WRONG
    assert judge(VALUE, NotFound()) is Outcome.MISSED
    assert judge(NotFound(), VALUE) is Outcome.INVENTED


def test_compare_document_records_page_agreement(tmp_path: Path) -> None:
    labels = load_answers(write_folder(tmp_path / "l", LABELS) / "award_a.json")
    predicted_fields = dict(LABELS["award_a"])
    predicted_fields["contract_value_paise"] = answer(
        "₹ 25 lakh", page=5
    )  # right value, wrong page
    predicted = parse_file(
        write_folder(tmp_path / "p", {"award_a": predicted_fields}) / "award_a.json"
    )
    results = {r.field: r for r in compare_document(labels, predicted)}
    assert all(r.outcome is Outcome.CORRECT for r in results.values())
    assert results["contract_value_paise"].same_page is False
    assert results["award_date"].same_page is True
    assert results["pbg_percentage"].same_page is None  # not_found has no page


def test_missing_prediction_file_makes_every_field_invalid(tmp_path: Path) -> None:
    labels = load_answers(write_folder(tmp_path / "l", LABELS) / "award_b.json")
    results = compare_document(labels, None)
    assert {r.outcome for r in results} == {Outcome.INVALID}
    assert {r.detail for r in results} == {"no prediction file"}


def test_missing_and_malformed_predicted_fields_are_invalid(tmp_path: Path) -> None:
    labels = load_answers(write_folder(tmp_path / "l", LABELS) / "award_b.json")
    fields = dict(LABELS["award_b"])
    del fields["contract_value_paise"]
    fields["pbg_percentage"] = answer("five")
    predicted = parse_file(write_folder(tmp_path / "p", {"award_b": fields}) / "award_b.json")
    results = {r.field: r for r in compare_document(labels, predicted)}
    assert results["contract_value_paise"].outcome is Outcome.INVALID
    assert results["contract_value_paise"].detail == "field missing from prediction"
    assert results["pbg_percentage"].outcome is Outcome.INVALID
    assert results["pbg_percentage"].detail.startswith("not a number")


def test_broken_prediction_structure_is_explained(tmp_path: Path) -> None:
    labels = load_answers(write_folder(tmp_path / "l", LABELS) / "award_b.json")
    path = tmp_path / "award_b.json"
    path.write_text('{"fields": {}}', encoding="utf-8")
    results = compare_document(labels, parse_file(path))
    assert {r.detail for r in results} == {'top level must have exactly "document" and "fields"'}


def test_field_stats_counts_and_ratios() -> None:
    stats = FieldStats()
    assert (stats.accuracy, stats.page_agreement) == (None, None)
    outcomes = [Outcome.CORRECT, Outcome.WRONG, Outcome.MISSED, Outcome.INVENTED, Outcome.INVALID]
    for outcome in outcomes:
        stats.add(FieldResult("d", "f", outcome, NOT_FOUND_ANSWER, None))
    stats.add(FieldResult("d", "f", Outcome.CORRECT, VALUE, VALUE, same_page=True))
    stats.add(FieldResult("d", "f", Outcome.CORRECT, VALUE, VALUE, same_page=False))
    assert (stats.total, stats.correct, stats.wrong, stats.missed) == (7, 3, 1, 1)
    assert (stats.invented, stats.invalid) == (1, 1)
    assert stats.accuracy == 3 / 7
    assert stats.page_agreement == 0.5


NOT_FOUND_ANSWER = NotFound()


def test_tally_sorts_fields_and_totals() -> None:
    results = [
        FieldResult("d1", "zeta", Outcome.CORRECT, VALUE, VALUE, same_page=True),
        FieldResult("d1", "alpha", Outcome.MISSED, VALUE, NotFound()),
        FieldResult("d2", "alpha", Outcome.CORRECT, NotFound(), NotFound()),
    ]
    per_field, overall = tally(results)
    assert list(per_field) == ["alpha", "zeta"]
    assert (per_field["alpha"].total, per_field["alpha"].missed) == (2, 1)
    assert (overall.total, overall.correct) == (3, 2)


def test_not_found_constant_is_the_claude_md_shape() -> None:
    assert NOT_FOUND == {"status": "not_found"}
