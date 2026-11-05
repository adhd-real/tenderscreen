import json
from decimal import Decimal
from pathlib import Path

import pytest

from tenderscreen.evals.answers import (
    MAX_FILE_BYTES,
    AnswerFileError,
    Found,
    NotFound,
    _field_error,
    _no_constants,
    _no_duplicates,
    _read_json,
    answer_files,
    is_todo,
    load_answers,
    parse_answer,
    parse_file,
    template_fields,
)

QUOTE = "Contract value: Rs. 25,000/-"


def write(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def found(
    value: object = "Rs. 25,000", page: object = 2, quote: object = QUOTE
) -> dict[str, object]:
    return {"value": value, "source_page": page, "quote": quote}


def test_parse_found_and_not_found() -> None:
    answer = parse_answer("contract_value_paise", found())
    assert answer == Found("Rs. 25,000", 2_500_000, 2, QUOTE)
    assert parse_answer("contract_value_paise", {"status": "not_found"}) == NotFound()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"status": "missing"}, "must be"),
        ({"value": 1, "source_page": 1}, "must be"),
        ({**found(), "confidence": "high"}, "must be"),
        ("Rs. 25,000", "must be"),
        (found(value="TODO"), "still says TODO"),
        (found(quote="TODO"), "still says TODO"),
        (found(page=0), "source_page"),
        (found(page="2"), "source_page"),
        (found(page=True), "source_page"),
        (found(quote="  "), "quote must be"),
        (found(quote=None), "quote must be"),
        (found(value="about five lakh"), "not a money amount"),
    ],
)
def test_parse_answer_rejects(raw: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_answer("contract_value_paise", raw)


def test_load_answers_strict(tmp_path: Path) -> None:
    path = write(
        tmp_path / "doc1.json",
        {"document": "doc1.pdf", "fields": {"contract_value_paise": found()}},
    )
    answers = load_answers(path)
    assert (answers.doc_id, answers.document) == ("doc1", "doc1.pdf")
    assert answers.fields["contract_value_paise"] == Found("Rs. 25,000", 2_500_000, 2, QUOTE)


def test_load_answers_lists_every_problem(tmp_path: Path) -> None:
    path = write(
        tmp_path / "bad.json",
        {
            "document": "",
            "fields": {"award_date": found(value="31/02/2026"), "number_of_bids": found("x")},
        },
    )
    with pytest.raises(AnswerFileError) as caught:
        load_answers(path)
    assert len(caught.value.problems) == 3
    assert caught.value.problems[0] == '"document" must name the source file'
    assert str(caught.value).startswith("bad.json: ")


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ([], 'top level must have exactly "document" and "fields"'),
        ({"document": "x.pdf"}, 'top level must have exactly "document" and "fields"'),
        ({"document": "x.pdf", "fields": {}}, '"fields" must be a non-empty object'),
        ({"document": "x.pdf", "fields": []}, '"fields" must be a non-empty object'),
    ],
)
def test_parse_file_structure_problems(tmp_path: Path, data: object, problem: str) -> None:
    parsed = parse_file(write(tmp_path / "x.json", data))
    assert parsed.problems == [problem]
    assert parsed.answers == {}


def test_document_name_still_todo_is_a_problem(tmp_path: Path) -> None:
    data = {"document": "TODO: file name", "fields": {"emd_paise": {"status": "not_found"}}}
    parsed = parse_file(write(tmp_path / "t.json", data))
    assert parsed.problems == ['"document" still says TODO: write the source file name']
    assert parsed.answers == {"emd_paise": NotFound()}


def test_parse_file_keeps_good_fields_and_reports_bad_ones(tmp_path: Path) -> None:
    path = write(
        tmp_path / "p.json",
        {
            "document": "p.pdf",
            "fields": {"emd_paise": found(), "award_date": found(value="soon")},
        },
    )
    parsed = parse_file(path)
    assert set(parsed.answers) == {"emd_paise"}
    assert parsed.field_errors["award_date"].startswith("not a date")


def test_floats_never_enter(tmp_path: Path) -> None:
    path = tmp_path / "f.json"
    path.write_text(
        '{"document": "f.pdf", "fields": {"emd_paise": '
        '{"value": 2500.5, "source_page": 1, "quote": "EMD"}}}',
        encoding="utf-8",
    )
    assert "integer paise or text" in parse_file(path).field_errors["emd_paise"]


@pytest.mark.parametrize("content", [b"{not json", b"\xff\xfe\x00"])
def test_unreadable_json_is_an_error(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "broken.json"
    path.write_bytes(content)
    with pytest.raises(AnswerFileError, match="not valid UTF-8 JSON"):
        parse_file(path)


@pytest.mark.slow
def test_oversized_file_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "huge.json"
    path.write_bytes(b" " * (MAX_FILE_BYTES + 1))
    with pytest.raises(AnswerFileError, match="larger than"):
        parse_file(path)


def test_answer_files_skips_templates_and_other_files(tmp_path: Path) -> None:
    for name in ("b.json", "a.json", "_template.json", "notes.md"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "dir.json").mkdir()
    assert list(answer_files(tmp_path)) == ["a", "b"]


def test_answer_files_needs_a_folder(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="is not a folder"):
        answer_files(tmp_path / "missing")


# Found in review.


@pytest.mark.parametrize(
    ("value", "todo"),
    [
        ("TODO", True),
        (" TODO ", True),
        ("TODO: file name in data/manual", True),
        ("Todo Enterprises", False),
        ("PHOTODOC.pdf", False),
        ("TODO.pdf", False),
        (7, False),
    ],
)
def test_is_todo_matches_only_the_placeholder(value: object, todo: bool) -> None:
    assert is_todo(value) is todo


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        ('{"document": "a.pdf", "document": "b.pdf", "fields": {}}', "repeated key 'document'"),
        (
            '{"document": "a.pdf", "fields": {"emd_paise": '
            '{"value": "Rs. 1", "value": "Rs. 2", "source_page": 1, "quote": "q"}}}',
            "repeated key 'value'",
        ),
        ('{"document": "a.pdf", "fields": {"x": NaN}}', "NaN is not a number"),
        ('{"document": "a.pdf", "fields": {"x": ' + "9" * 5000 + "}}", "not valid UTF-8 JSON"),
        ("[" * 200_000, "not valid UTF-8 JSON"),
    ],
)
def test_hostile_json_is_a_clean_file_error(tmp_path: Path, content: str, problem: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(AnswerFileError, match=problem):
        parse_file(path)


def test_byte_order_mark_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "bom.json"
    data = {"document": "a.pdf", "fields": {"emd_paise": {"status": "not_found"}}}
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(data).encode())
    assert parse_file(path).answers == {"emd_paise": NotFound()}


def test_source_page_todo_is_caught() -> None:
    with pytest.raises(ValueError, match="still says TODO"):
        parse_answer("emd_paise", found(page="TODO"))


def test_labels_must_write_money_as_text() -> None:
    with pytest.raises(ValueError, match="write money as text"):
        parse_answer("emd_paise", found(value=2_500_000), label=True)
    assert parse_answer("emd_paise", found(value=2_500_000)) == Found(
        2_500_000, 2_500_000, 2, QUOTE
    )  # predictions may give int paise


def test_contact_errors_never_echo_the_value(tmp_path: Path) -> None:
    path = write(
        tmp_path / "p.json",
        {"document": "p.pdf", "fields": {"contact_phone": found(value=None)}},
    )
    errors = parse_file(path).field_errors
    assert errors == {"contact_phone": "invalid answer (details hidden: contact data)"}


def test_unreadable_value_is_marked_as_claimed(tmp_path: Path) -> None:
    path = write(
        tmp_path / "p.json",
        {
            "document": "p.pdf",
            "fields": {"pbg_percentage": found(value="five"), "award_date": {"bad": 1}},
        },
    )
    parsed = parse_file(path)
    assert parsed.claimed == frozenset({"pbg_percentage"})
    assert set(parsed.field_errors) == {"pbg_percentage", "award_date"}


def test_unknown_field_names_are_caught_against_the_template(tmp_path: Path) -> None:
    path = write(
        tmp_path / "doc.json",
        {"document": "doc.pdf", "fields": {"award_dat": {"status": "not_found"}}},
    )
    with pytest.raises(AnswerFileError, match=r"award_dat: not a field in _template\.json"):
        load_answers(path, known_fields=frozenset({"award_date"}))
    assert load_answers(path).fields == {"award_dat": NotFound()}  # no template: allowed


def test_template_fields(tmp_path: Path) -> None:
    assert template_fields(tmp_path) is None
    write(tmp_path / "_template.json", {"document": "TODO", "fields": {"a": 1, "b": 2}})
    assert template_fields(tmp_path) == frozenset({"a", "b"})
    write(tmp_path / "_template.json", {"document": "TODO"})
    with pytest.raises(AnswerFileError, match='needs a "fields" object'):
        template_fields(tmp_path)


def test_read_json_keeps_decimals_exact(tmp_path: Path) -> None:
    path = tmp_path / "d.json"
    path.write_text('{"a": 0.1, "b": 7}', encoding="utf-8")
    assert _read_json(path) == {"a": Decimal("0.1"), "b": 7}


def test_read_json_rejects_bad_bytes(tmp_path: Path) -> None:
    path = tmp_path / "latin1.json"
    path.write_bytes(b'{"a": "caf\xe9"}')
    with pytest.raises(AnswerFileError, match="not valid UTF-8 JSON: 'utf-8' codec"):
        _read_json(path)


def test_no_duplicates_and_no_constants() -> None:
    assert _no_duplicates([("a", 1), ("b", 2)]) == {"a": 1, "b": 2}
    with pytest.raises(ValueError, match="repeated key 'a'"):
        _no_duplicates([("b", 1), ("a", 2), ("a", 3)])
    with pytest.raises(ValueError, match="Infinity is not a number"):
        _no_constants("Infinity")


def test_field_error_is_short_and_private_fields_are_hidden() -> None:
    assert _field_error("award_date", ValueError("x" * 500)) == "x" * 200
    assert _field_error("mobile", ValueError("0000")) == (
        "invalid answer (details hidden: contact data)"
    )
