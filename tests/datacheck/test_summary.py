from tenderscreen.datacheck.summary import breakdown, field_counts, render_summary, yes_no


def test_yes_no() -> None:
    assert [yes_no(v) for v in ("y", " YES ", "1", "हाँ", "n", "No", "", "maybe")] == [
        "yes",
        "yes",
        "yes",
        "yes",
        "no",
        "no",
        "blank",
        "unclear",
    ]


ROWS = [
    {"portal": "CPPP", "notice_type": "award", "has_award_date": "y", "has_contract_value": "y"},
    {"portal": "GeM", "notice_type": "award", "has_award_date": "n", "has_contract_value": "y"},
    {"portal": "cppp", "notice_type": "tender", "has_award_date": "", "has_contract_value": "?"},
]


def test_field_counts() -> None:
    counts = field_counts(ROWS)
    assert counts["has_award_date"] == {"yes": 1, "no": 1, "blank": 1}
    assert counts["has_contract_value"] == {"yes": 2, "unclear": 1}
    assert counts["has_loa_date"] == {"blank": 3}


def test_breakdown_folds_case_and_names_blanks() -> None:
    assert breakdown(ROWS, "portal") == {"cppp": 2, "gem": 1}
    assert breakdown(ROWS, "language") == {"(blank)": 3}


def test_render_summary() -> None:
    text = render_summary(ROWS, ["`x.pdf`: unreadable"], "2026-11-05T06:00+05:30")
    assert "**3 records**" in text
    assert "| award date | 1 | 1 | 1 | 0 | 33% |" in text
    assert "| contract value | 2 | 0 | 0 | 1 | 67% |" in text
    assert "- **portal**: cppp: 2, gem: 1" in text
    assert "## Problems\n\n- `x.pdf`: unreadable" in text


def test_render_summary_of_no_records() -> None:
    text = render_summary([], [], "now")
    assert "**0 records**" in text
    assert "| award date | 0 | 0 | 0 | 0 | n/a |" in text
    assert "## Problems" not in text
