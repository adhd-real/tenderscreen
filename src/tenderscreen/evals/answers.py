"""Load and strictly validate one document's answers (labels or predictions).

A file ``<document id>.json`` looks like::

    {
      "document": "pwd_award_2026_17.pdf",
      "fields": {
        "contract_value_paise": {"value": "Rs. 25,00,000", "source_page": 2,
                                 "quote": "Contract value: Rs. 25,00,000/-"},
        "pbg_percentage": {"status": "not_found"}
      }
    }

Each field is either ``{"value", "source_page", "quote"}`` or
``{"status": "not_found"}`` (CLAUDE.md rule 3). Unknown or missing keys, an
empty quote, a page below 1, a value that does not parse for its field kind, or
a leftover "TODO" are all errors.
"""

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from tenderscreen.evals.fields import Comparable, comparable, field_kind

MAX_FILE_BYTES: Final = 5 * 1024 * 1024
TODO: Final = "TODO"
_FOUND_KEYS: Final = frozenset({"value", "source_page", "quote"})
_NOT_FOUND: Final = {"status": "not_found"}


class AnswerFileError(Exception):
    """An answers file is malformed. ``problems`` lists every issue found."""

    def __init__(self, path: Path, problems: list[str]) -> None:
        super().__init__(f"{path.name}: " + "; ".join(problems))
        self.path = path
        self.problems = problems


@dataclass(frozen=True, slots=True)
class Found:
    raw_value: object
    value: Comparable
    source_page: int
    quote: str


@dataclass(frozen=True, slots=True)
class NotFound:
    pass


type Answer = Found | NotFound


@dataclass(frozen=True, slots=True)
class DocumentAnswers:
    doc_id: str  # the file name without .json
    document: str  # the source document's file name, as written in the file
    fields: dict[str, Answer]


def parse_answer(name: str, raw: object) -> Answer:
    """One field's answer; ``ValueError`` with a readable reason if malformed."""
    if raw == _NOT_FOUND:
        return NotFound()
    if not isinstance(raw, dict) or set(raw) != _FOUND_KEYS:
        raise ValueError(
            'must be {"value": ..., "source_page": ..., "quote": ...} or {"status": "not_found"}'
        )
    value, page, quote = raw["value"], raw["source_page"], raw["quote"]
    if TODO in (value, quote):
        raise ValueError("still says TODO: fill it in, or mark it not_found")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError(f"source_page must be a page number from 1, not {page!r}")
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("quote must be the exact text from the page, not empty")
    return Found(value, comparable(field_kind(name), value), page, quote)


@dataclass(frozen=True, slots=True)
class ParsedFile:
    """Every field that parsed, every field that did not (with why), and file-level problems."""

    doc_id: str
    document: str
    answers: dict[str, Answer]
    field_errors: dict[str, str]
    problems: list[str]


def _read_json(path: Path) -> Any:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise AnswerFileError(path, [f"larger than {MAX_FILE_BYTES} bytes"])
    try:
        # parse_float=Decimal: no float ever enters, so money stays exact.
        return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnswerFileError(path, [f"not valid UTF-8 JSON: {exc}"]) from exc


def parse_file(path: Path) -> ParsedFile:
    """Parse an answers file field by field; only unreadable JSON raises."""
    data = _read_json(path)
    if not isinstance(data, dict) or set(data) != {"document", "fields"}:
        problem = 'top level must have exactly "document" and "fields"'
        return ParsedFile(path.stem, "", {}, {}, [problem])
    document, fields = data["document"], data["fields"]
    problems = []
    if not isinstance(document, str) or not document.strip():
        problems.append('"document" must name the source file')
        document = ""
    elif TODO in document:
        problems.append('"document" still says TODO: write the source file name')
    if not isinstance(fields, dict) or not fields:
        problems.append('"fields" must be a non-empty object')
        fields = {}
    answers: dict[str, Answer] = {}
    errors: dict[str, str] = {}
    for name, raw in fields.items():
        try:
            answers[name] = parse_answer(name, raw)
        except ValueError as exc:
            errors[name] = str(exc)
    return ParsedFile(path.stem, document, answers, errors, problems)


def load_answers(path: Path) -> DocumentAnswers:
    """Read a labels file strictly; ``AnswerFileError`` lists every problem."""
    parsed = parse_file(path)
    problems = parsed.problems + [f"{name}: {why}" for name, why in parsed.field_errors.items()]
    if problems:
        raise AnswerFileError(path, problems)
    return DocumentAnswers(parsed.doc_id, parsed.document, parsed.answers)


def answer_files(directory: Path) -> dict[str, Path]:
    """``<doc id>.json`` files in ``directory`` (files starting with "_" are templates)."""
    if not directory.is_dir():
        raise FileNotFoundError(f"{directory} is not a folder")
    return {
        path.stem: path
        for path in sorted(directory.glob("*.json"))
        if not path.name.startswith("_") and path.is_file()
    }
