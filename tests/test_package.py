import tomllib
from pathlib import Path

import tenderscreen

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version_matches_pyproject() -> None:
    expected = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert tenderscreen.__version__ == expected


def test_package_imports_from_src_layout() -> None:
    package_dir = Path(tenderscreen.__file__).resolve().parent
    assert package_dir.parent.name == "src"
