import json
from datetime import datetime
from pathlib import Path

from tenderscreen.evals import evaluate
from tenderscreen.evals.answers import Found, NotFound
from tenderscreen.evals.compare import FieldStats
from tenderscreen.evals.report import (
    _percent,
    _ratio,
    _stats_json,
    _stats_row,
    code,
    format_paise,
    plain,
    render_markdown,
    show,
    show_detail,
    summary_json,
)
from tenderscreen.ist import IST
from tests.evals.dataset import LABELS, NOT_FOUND, answer, write_folder

WHEN = datetime(2026, 11, 5, 6, 0, tzinfo=IST)


def render(
    tmp_path: Path, predictions: dict[str, dict[str, object]]
) -> tuple[str, dict[str, object]]:
    labels = write_folder(tmp_path / "labels", LABELS)
    evaluation = evaluate(labels, write_folder(tmp_path / "pred", predictions))
    args = (evaluation.results, evaluation.per_field, evaluation.overall, evaluation.inventory)
    return render_markdown(*args, WHEN), summary_json(*args, WHEN)


def mistaken() -> dict[str, dict[str, object]]:
    predictions = {doc: dict(fields) for doc, fields in LABELS.items()}
    predictions["award_a"]["successful_bidder_name"] = answer("Wrong | Name Ltd", page=2)
    predictions["award_a"]["contact_details"] = answer("Someone, 0000-111111")
    predictions["award_b"]["number_of_bids"] = answer(9)
    del predictions["award_b"]["pbg_percentage"]
    return predictions


def test_markdown_has_table_explanations_and_mistakes(tmp_path: Path) -> None:
    markdown, _ = render(tmp_path, mistaken())
    assert "| `successful_bidder_name` | text | 2 | 1 | 50% | 1 | 0 | 0 | 0 | 100% |" in markdown
    assert "| **All fields** |" in markdown
    assert "- **Invented**: the label says not_found" in markdown
    assert "### `award_a`" in markdown
    assert "Wrong / Name Ltd (p. 2)" in markdown  # pipes escaped for the table
    assert "| `number_of_bids` | invented | not_found | 9 (p. 1) |" in markdown
    assert (
        "| `pbg_percentage` | invalid | 5 (p. 1) | invalid: field missing from prediction |"
        in markdown
    )


def test_contact_details_never_appear_in_reports(tmp_path: Path) -> None:
    markdown, summary = render(tmp_path, mistaken())
    assert "0000-111111" not in markdown
    assert "0000-000000" not in markdown
    assert "(hidden: contact data)" in markdown
    assert "0000" not in str(summary)


def test_summary_has_counts_but_no_values(tmp_path: Path) -> None:
    _, summary = render(tmp_path, mistaken())
    assert summary["documents"] == {
        "labelled": 2,
        "predicted": 2,
        "without_prediction": [],
        "without_label": [],
    }
    fields = summary["fields"]
    assert isinstance(fields, dict)
    assert fields["number_of_bids"]["invented"] == 1
    assert fields["number_of_bids"]["kind"] == "integer"
    assert "Wrong" not in str(summary)
    assert "Synthetic Builders" not in str(summary)


def test_problems_section_lists_unpaired_files(tmp_path: Path) -> None:
    predictions = {"award_a": dict(LABELS["award_a"]), "stray": dict(LABELS["award_b"])}
    predictions["award_a"]["unexpected"] = NOT_FOUND
    markdown, _ = render(tmp_path, predictions)
    assert "- No prediction for labelled document `award_b`" in markdown
    assert "- Prediction without labels (ignored): `stray`" in markdown
    assert "- Predicted field not in labels (ignored): `award_a: unexpected`" in markdown


def test_show() -> None:
    assert show("emd_paise", None) == "(none)"
    assert show("emd_paise", NotFound()) == "not_found"
    assert show("emd_paise", Found("Rs. 5", 500, 3, "q")) == "\u20b9 5.00 (p. 3)"
    assert show("tender_id", Found("ABC/2026", "abc/2026", 3, "q")) == "ABC/2026 (p. 3)"
    long = Found("x" * 100, "x" * 100, 1, "q")
    assert show("name", long) == "x" * 57 + "... (p. 1)"
    assert show("contact_phone", Found("0000", "0000", 1, "q")) == "(hidden: contact data)"


def test_format_paise_uses_indian_grouping() -> None:
    assert format_paise(0) == "\u20b9 0.00"
    assert format_paise(50) == "\u20b9 0.50"
    assert format_paise(123_456) == "\u20b9 1,234.56"
    assert format_paise(250_000_050) == "\u20b9 25,00,000.50"
    assert format_paise(1_234_567_890_00) == "\u20b9 1,23,45,67,890.00"


def test_money_is_shown_in_rupees() -> None:
    answer = Found("Rs. 25 lakh", 250_000_000, 2, "q")
    assert show("contract_value_paise", answer) == "\u20b9 25,00,000.00 (p. 2)"


# Found in review: names and details come from files anyone can edit, so they
# must not break the Markdown, and private details must never be shown.


def test_plain_flattens_escapes_and_shortens() -> None:
    assert plain("short") == "short"
    assert plain("a\n| b`c\t d") == "a / b'c d"
    assert plain("x" * 60) == "x" * 60
    assert plain("x" * 61) == "x" * 57 + "..."
    assert plain("abcdefgh", limit=5) == "ab..."


def test_code_cannot_break_out_of_inline_code_or_a_table() -> None:
    assert code("award_a") == "`award_a`"
    assert code("x`](http://bad)|\n# Heading") == "`x'](http://bad)/ # Heading`"
    assert code("y" * 100) == "`" + "y" * 77 + "...`"


def test_show_detail_hides_contact_data_and_shortens() -> None:
    assert show_detail("award_date", "not a date") == "not a date"
    assert show_detail("award_date", "z" * 200) == "z" * 117 + "..."
    assert show_detail("contact_phone", "0000-111111") == "(details hidden: contact data)"


def test_percent_and_ratio_handle_no_data() -> None:
    assert _percent(None) == "n/a"
    assert _percent(0.0) == "0%"
    assert _percent(2 / 3) == "67%"
    assert _percent(1.0) == "100%"
    assert _ratio(None) is None
    assert _ratio(2 / 3) == 0.6667
    assert _ratio(1.0) == 1.0


def test_stats_row_and_json_with_and_without_data() -> None:
    stats = FieldStats(total=4, correct=2, wrong=1, missed=1, pages_checked=2, pages_matched=1)
    row = "| `f` | money | 4 | 2 | 50% | 1 | 1 | 0 | 0 | 50% |"
    assert _stats_row("`f`", "money", stats) == row
    assert _stats_json(stats) == {
        "labelled": 4,
        "correct": 2,
        "accuracy": 0.5,
        "wrong": 1,
        "missed": 1,
        "invented": 0,
        "invalid": 0,
        "page_agreement": 0.5,
    }
    empty = FieldStats()
    assert _stats_row("x", "", empty) == "| x |  | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a |"
    assert _stats_json(empty)["accuracy"] is None
    assert _stats_json(empty)["page_agreement"] is None


def test_page_agreement_is_n_a_when_nothing_was_correct(tmp_path: Path) -> None:
    predictions = {doc: dict(fields) for doc, fields in LABELS.items()}
    predictions["award_a"]["successful_bidder_name"] = answer("Other Name")
    predictions["award_b"]["successful_bidder_name"] = answer("Another Name")
    markdown, summary = render(tmp_path, predictions)
    assert "| `successful_bidder_name` | text | 2 | 0 | 0% | 2 | 0 | 0 | 0 | n/a |" in markdown
    fields = summary["fields"]
    assert isinstance(fields, dict)
    assert fields["successful_bidder_name"]["page_agreement"] is None


def test_hostile_names_and_values_stay_inside_their_cells(tmp_path: Path) -> None:
    labels = write_folder(tmp_path / "labels", {"doc": {"tender_id": answer("T-1")}})
    hostile = "T-2 |\n## Injected heading\n`code`"
    predictions = write_folder(
        tmp_path / "pred",
        {"doc": {"tender_id": answer(hostile), "x|y\n# z": answer("v")}},
    )
    evaluation = evaluate(labels, predictions)
    args = (evaluation.results, evaluation.per_field, evaluation.overall, evaluation.inventory)
    markdown = render_markdown(*args, WHEN)
    assert "\n## Injected heading" not in markdown
    row = "| `tender_id` | wrong | T-1 (p. 1) | T-2 / ## Injected heading 'code' (p. 1) |"
    assert row in markdown
    assert "- Predicted field not in labels (ignored): `doc: x/y # z`" in markdown


def test_unreadable_and_invalid_details_are_shown_safely(tmp_path: Path) -> None:
    predictions = {doc: dict(fields) for doc, fields in LABELS.items()}
    predictions["award_a"]["award_date"] = answer("within 21 days of LoA")  # unreadable
    predictions["award_a"]["contact_details"] = answer(None)  # invalid contact value
    markdown, _ = render(tmp_path, predictions)
    assert "| `award_date` | wrong | 15/10/2026 (p. 1) | unreadable value: not a date" in markdown
    assert "| `contact_details` | wrong | (hidden: contact data) |" in markdown
    assert "(details hidden: contact data) |" in markdown
    assert "None" not in markdown.split("## Mistakes by document")[1]


def test_document_name_mismatch_is_a_problem(tmp_path: Path) -> None:
    labels = write_folder(tmp_path / "labels", LABELS)
    predictions = write_folder(tmp_path / "pred", LABELS)
    data = json.loads((predictions / "award_a.json").read_text(encoding="utf-8"))
    data["document"] = "other|file.pdf"
    (predictions / "award_a.json").write_text(json.dumps(data), encoding="utf-8")
    evaluation = evaluate(labels, predictions)
    args = (evaluation.results, evaluation.per_field, evaluation.overall, evaluation.inventory)
    markdown = render_markdown(*args, WHEN)
    assert (
        "- Document name differs (check the pairing): award_a: label names award_a.pdf, "
        "prediction names other/file.pdf" in markdown
    )
    assert summary_json(*args, WHEN)["document_mismatches"] == 1
