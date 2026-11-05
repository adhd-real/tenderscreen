from datetime import datetime
from pathlib import Path

import pytest

from tenderscreen.ist import IST
from tenderscreen.store import DEFAULT_ROOT, StoreError, open_store, save_document
from tenderscreen.store.facade import DATABASE_NAME

WHEN = datetime(2026, 10, 15, 17, 0, tzinfo=IST)


def test_default_root_is_the_git_ignored_raw_folder() -> None:
    assert Path("data/raw") == DEFAULT_ROOT
    gitignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8")
    assert "data/raw/" in gitignore


def test_save_document_function_uses_root(tmp_path: Path) -> None:
    doc = save_document(b"%PDF-1.7 x", "https://example.org/x.pdf", WHEN, root=tmp_path)
    assert doc.path == tmp_path / doc.sha256
    assert (tmp_path / DATABASE_NAME).is_file()
    again = save_document(b"%PDF-1.7 x", "https://example.org/y.pdf", WHEN, root=tmp_path)
    assert len(again.sources) == 2


def test_open_store_context_manager_closes(tmp_path: Path) -> None:
    with open_store(tmp_path / "nested" / "raw") as store:
        store.documents.save_document(b"abc", "https://example.org/a", WHEN)
    with pytest.raises(StoreError, match="closed"):
        store.documents.get("a" * 64)
