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
``{"status": "not_found"}`` (CLAUDE.md rule 3). Unknown or missing keys,
repeated keys, an empty quote, a page below 1, a value that does not parse for
its field kind, or a leftover "TODO" are all errors. Labels must also write
money as text ("Rs. 25,00,000"): a bare number would be read as paise.
"""

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from tenderscreen.evals.fields import (
    Comparable,
    FieldKind,
    comparable,
    field_kind,
    is_private_field,
)

MAX_FILE_BYTES: Final = 5 * 1024 * 1024
MAX_ERROR_CHARS: Final = 200
TODO: Final = "TODO"
_FOUND_KEYS: Final = frozenset({"value", "source_page", "quote"})
_NOT_FOUND: Final = {"status": "not_found"}


class InputEchoError(ValueError):
    """A field error whose message repeats part of the input (hidden for contact fields)."""


class UnreadableValueError(InputEchoError):
    """The answer is well formed, but its value does not parse for the field's kind."""


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


def is_todo(value: object) -> bool:
    """True for the template's placeholders: "TODO" or "TODO: ..." (not "Todo Ltd")."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return text == TODO or text.startswith(TODO + ":")


def parse_answer(name: str, raw: object, *, label: bool = False) -> Answer:
    """One field's answer; ``ValueError`` with a readable reason if malformed."""
    if raw == _NOT_FOUND:
        return NotFound()
    if not isinstance(raw, dict) or set(raw) != _FOUND_KEYS:
        raise ValueError(
            'must be {"value": ..., "source_page": ..., "quote": ...} or {"status": "not_found"}'
        )
    value, page, quote = raw["value"], raw["source_page"], raw["quote"]
    if any(is_todo(part) for part in (value, page, quote)):
        raise ValueError("still says TODO: fill it in, or mark it not_found")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise InputEchoError(f"source_page must be a page number from 1, not {page!r}")
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("quote must be the exact text from the page, not empty")
    kind = field_kind(name)
    if label and kind is FieldKind.MONEY and not isinstance(value, str):
        raise ValueError(
            'write money as text, for example "Rs. 25,00,000" '
            "(a bare number would be read as paise)"
        )
    try:
        return Found(value, comparable(kind, value), page, quote)
    except ValueError as exc:
        raise UnreadableValueError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class ParsedFile:
    """Every field that parsed, every field that did not (with why), and file-level problems."""

    doc_id: str
    document: str
    answers: dict[str, Answer]
    field_errors: dict[str, str]
    problems: list[str]
    # Well-formed answers whose value could not be read: the file claims a
    # value exists, which matters when the label says not_found. A malformed
    # citation (page, quote) is not a claim; that field is simply invalid.
    claimed: frozenset[str] = frozenset()


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """A JSON object, refusing a repeated key (one pass: a 5 MB file stays fast)."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"repeated key {key!r}")
        result[key] = value
    return result


def _no_constants(name: str) -> object:
    raise ValueError(f"{name} is not a number")


def _read_json(path: Path) -> Any:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise AnswerFileError(path, [f"larger than {MAX_FILE_BYTES} bytes"])
    try:
        data = json.loads(
            path.read_text(encoding="utf-8-sig"),  # tolerate a byte-order mark
            parse_float=Decimal,  # no float ever enters, so money stays exact
            parse_constant=_no_constants,  # NaN and Infinity are refused
            object_pairs_hook=_no_duplicates,  # the last of two "value" keys must not win
        )
        # JSON may spell a lone surrogate ("\ud800"), which is not text and
        # cannot be written to the report: UnicodeEncodeError is a ValueError.
        json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        return data
    except UnicodeEncodeError as exc:
        problem = "not valid UTF-8 JSON: a \\u escape spells half a character (e.g. \\ud800)"
        raise AnswerFileError(path, [problem]) from exc
    except (UnicodeDecodeError, ValueError, ArithmeticError, RecursionError) as exc:
        detail = str(exc)[:MAX_ERROR_CHARS] or type(exc).__name__
        raise AnswerFileError(path, [f"not valid UTF-8 JSON: {detail}"]) from exc


def _field_error(name: str, exc: ValueError) -> str:
    """The reason a field failed, without ever echoing contact data."""
    if is_private_field(name) and isinstance(exc, InputEchoError):
        return "invalid answer (details hidden: contact data)"
    return str(exc)[:MAX_ERROR_CHARS]


def parse_file(path: Path, *, label: bool = False) -> ParsedFile:
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
    elif is_todo(document):
        problems.append('"document" still says TODO: write the source file name')
    if not isinstance(fields, dict) or not fields:
        problems.append('"fields" must be a non-empty object')
        fields = {}
    answers: dict[str, Answer] = {}
    errors: dict[str, str] = {}
    claimed = set()
    for name, raw in fields.items():
        try:
            answers[name] = parse_answer(name, raw, label=label)
        except ValueError as exc:
            errors[name] = _field_error(name, exc)
            if isinstance(exc, UnreadableValueError):
                claimed.add(name)
    return ParsedFile(path.stem, document, answers, errors, problems, frozenset(claimed))


def load_answers(path: Path, *, known_fields: frozenset[str] | None = None) -> DocumentAnswers:
    """Read a labels file strictly; ``AnswerFileError`` lists every problem.

    ``known_fields``: the field names a labels file may use (from the template);
    any other name is reported, since it is most likely a typo.
    """
    parsed = parse_file(path, label=True)
    problems = parsed.problems + [f"{name}: {why}" for name, why in parsed.field_errors.items()]
    if known_fields is not None:
        problems += [
            f"{name}: not a field in _template.json (a typo?)"
            for name in sorted(set(parsed.answers) | set(parsed.field_errors))
            if name not in known_fields
        ]
    if problems:
        raise AnswerFileError(path, problems)
    return DocumentAnswers(parsed.doc_id, parsed.document, parsed.answers)


def template_fields(directory: Path) -> frozenset[str] | None:
    """Field names in ``directory/_template.json``, or None if there is no template."""
    template = directory / "_template.json"
    if not template.is_file():
        return None
    data = _read_json(template)
    if not isinstance(data, dict) or not isinstance(data.get("fields"), dict):
        raise AnswerFileError(template, ['the template needs a "fields" object'])
    return frozenset(data["fields"])


def answer_files(directory: Path) -> dict[str, Path]:
    """``<doc id>.json`` files in ``directory`` (files starting with "_" are templates)."""
    if not directory.is_dir():
        raise FileNotFoundError(f"{directory} is not a folder")
    return {
        path.stem: path
        for path in sorted(directory.glob("*.json"))
        if not path.name.startswith("_") and path.is_file()
    }
