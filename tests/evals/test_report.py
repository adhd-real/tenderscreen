from datetime import datetime
from pathlib import Path

from tenderscreen.evals import evaluate
from tenderscreen.evals.answers import Found, NotFound
from tenderscreen.evals.report import format_paise, render_markdown, show, summary_json
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
