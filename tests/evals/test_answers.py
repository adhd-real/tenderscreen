import json
from pathlib import Path

import pytest

from tenderscreen.evals.answers import (
    MAX_FILE_BYTES,
    AnswerFileError,
    Found,
    NotFound,
    answer_files,
    load_answers,
    parse_answer,
    parse_file,
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
    data = {"document": "TODO.pdf", "fields": {"emd_paise": {"status": "not_found"}}}
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
