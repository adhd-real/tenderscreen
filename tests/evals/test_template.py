"""The committed labels template must parse, and must refuse to pass unfilled."""

import json
from pathlib import Path

from tenderscreen.evals.answers import parse_file
from tenderscreen.evals.fields import FieldKind, field_kind

TEMPLATE = Path(__file__).resolve().parents[2] / "data" / "labels" / "_template.json"


def test_template_is_valid_json_with_every_field_marked_todo() -> None:
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert set(data) == {"document", "fields"}
    assert all(field["value"] == "TODO" for field in data["fields"].values())


def test_unfilled_template_is_rejected_field_by_field() -> None:
    parsed = parse_file(TEMPLATE)
    assert parsed.answers == {}
    assert len(parsed.field_errors) == 12
    for name, why in parsed.field_errors.items():
        expected = (
            "details hidden: contact data" if name == "contact_details" else "still says TODO"
        )
        assert expected in why, name
    assert parsed.problems == ['"document" still says TODO: write the source file name']


def test_template_covers_every_field_kind() -> None:
    names = json.loads(TEMPLATE.read_text(encoding="utf-8"))["fields"]
    assert {field_kind(name) for name in names} == set(FieldKind)
