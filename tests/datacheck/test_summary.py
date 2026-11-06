from tenderscreen.datacheck.summary import (
    breakdown,
    by_portal,
    cell,
    code,
    field_counts,
    render_summary,
    yes_no,
)


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
    text = render_summary(ROWS, ["`x.pdf`: unreadable"])
    assert "From `records.csv`: **3 records**." in text
    assert "| award date | 1 | 1 | 1 | 0 | 33% |" in text
    assert "| contract value | 2 | 0 | 0 | 1 | 67% |" in text
    assert "- **portal**: cppp: 2, gem: 1" in text
    assert "## Problems\n\n- `x.pdf`: unreadable" in text


def test_render_summary_of_no_records() -> None:
    text = render_summary([], [])
    assert "**0 records**" in text
    assert "| award date | 0 | 0 | 0 | 0 | n/a |" in text
    assert "## Problems" not in text
    assert "## Records by portal" not in text


# Found in review.


def test_summary_has_no_timestamp_so_reruns_do_not_change_it() -> None:
    assert render_summary(ROWS, []) == render_summary([dict(r) for r in ROWS], [])
    assert "Generated" not in render_summary(ROWS, [])


def test_cell_and_code_keep_markdown_intact() -> None:
    assert cell("  CPPP |\n central ") == "CPPP / central"
    assert code("odd`name|x.pdf") == "`odd'name/x.pdf`"
    assert code("gem/bid 7.pdf") == "`gem/bid 7.pdf`"


def test_by_portal_counts_notice_types_and_fields() -> None:
    rows = [
        *ROWS,
        {"portal": "GeM", "notice_type": "Corrigendum", "has_award_date": "y"},
        {"portal": "", "notice_type": ""},
    ]
    text = "\n".join(by_portal(rows))
    assert "| Portal | award | tender | other | (blank) | Total |" in text
    assert "| (blank) | 0 | 0 | 0 | 1 | 1 |" in text
    assert "| cppp | 1 | 1 | 0 | 0 | 2 |" in text
    assert "| gem | 1 | 0 | 1 | 0 | 2 |" in text
    assert (
        "| Portal | Records | award date | successful bidder name | contract value "
        "| number of bids | loa date | pbg percentage or deadline | contact details |"
    ) in text
    assert "| cppp | 2 | 1 | 0 | 1 | 0 | 0 | 0 | 0 |" in text
    assert "| gem | 2 | 1 | 0 | 1 | 0 | 0 | 0 | 0 |" in text


def test_by_portal_of_no_rows_is_empty() -> None:
    assert by_portal([]) == []


def test_typed_values_cannot_break_the_summary_tables() -> None:
    rows = [{"portal": "CPPP | fake\n## Heading", "notice_type": "award"}]
    text = render_summary(rows, [])
    assert "\n## Heading" not in text
    assert "| cppp / fake ## heading | 1 | 0 | 0 | 0 | 1 |" in text
