"""Guard the CI quality gates against being weakened by accident."""

import tomllib
from pathlib import Path
from typing import Any, cast

import coverage
import pytest

from tests.conftest import pytest_collection_modifyitems, unmarked_ocr_tests

ROOT = Path(__file__).resolve().parents[1]


def load_pyproject() -> dict[str, Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_coverage_gate_is_at_least_90_percent_of_src() -> None:
    # Ask coverage.py itself, so the values it will actually enforce are checked.
    cov = coverage.Coverage(config_file=str(ROOT / "pyproject.toml"))
    fail_under = cov.get_option("report:fail_under")
    assert isinstance(fail_under, int | float)
    assert fail_under >= 90
    # With precision 0, coverage rounds 89.5% up to 90% and passes.
    assert cov.get_option("report:precision") == 2
    assert cov.get_option("run:source_pkgs") == ["tenderscreen"]
    assert cov.get_option("report:include_namespace_packages") is True


def test_pytest_always_measures_coverage() -> None:
    addopts = load_pyproject()["tool"]["pytest"]["ini_options"]["addopts"]
    assert "--cov" in addopts
    assert not any(opt.startswith(("--no-cov", "--cov-fail-under")) for opt in addopts)


def test_no_other_coverage_config_overrides_pyproject() -> None:
    # coverage.py prefers .coveragerc, then setup.cfg / tox.ini sections.
    assert not (ROOT / ".coveragerc").exists()
    for name in ("setup.cfg", "tox.ini"):
        path = ROOT / name
        assert not path.exists() or "coverage:" not in path.read_text(encoding="utf-8")


def test_ci_runs_tests_and_licence_check() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "run: pytest\n" in ci
    assert "run: python -m tenderscreen.devtools.licence_check\n" in ci


def test_ci_installs_tesseract_and_never_skips_ocr_tests() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "apt-get install -y tesseract-ocr tesseract-ocr-hin fonts-noto-core" in ci
    assert 'TENDERSCREEN_REQUIRE_OCR: "1"' in ci


# Task A, item 3: a "slow" marker. Only the post-edit hook leaves slow tests out.


def test_slow_marker_is_registered_and_markers_are_strict() -> None:
    options = load_pyproject()["tool"]["pytest"]["ini_options"]
    assert "--strict-markers" in options["addopts"]  # a misspelt marker is an error
    assert any(marker.startswith("slow:") for marker in options["markers"])


def test_plain_pytest_and_ci_run_the_full_suite() -> None:
    addopts = load_pyproject()["tool"]["pytest"]["ini_options"]["addopts"]
    assert not any(opt == "-m" or opt.startswith(("-m ", "-m=")) for opt in addopts)
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "run: pytest\n" in ci
    assert "not slow" not in ci
    assert "PYTEST_ADDOPTS" not in ci


def test_post_edit_hook_runs_only_fast_tests_without_the_coverage_gate() -> None:
    hook = (ROOT / ".claude" / "hooks" / "post-edit.sh").read_text(encoding="utf-8")
    assert '"$bin/pytest" -x -q --no-header -m "not slow" --no-cov' in hook


class FakeItem:
    """Just enough of a collected test for the slow-marker guard."""

    def __init__(self, nodeid: str, fixturenames: tuple[str, ...], *, slow: bool) -> None:
        self.nodeid = nodeid
        self.fixturenames = fixturenames
        self.slow = slow

    def get_closest_marker(self, name: str) -> object | None:
        return object() if name == "slow" and self.slow else None


class FakeDoctest:
    """A collected item without fixtures, such as a doctest."""

    nodeid = "doc.txt::doc"


ITEMS: list[FakeItem | FakeDoctest] = [
    FakeItem("t.py::test_ocr_marked", ("tmp_path", "tesseract"), slow=True),
    FakeItem("t.py::test_ocr_unmarked", ("tesseract",), slow=False),
    FakeItem("t.py::test_fast", ("tmp_path",), slow=False),
    FakeItem("t.py::test_big_file", ("tmp_path",), slow=True),
    FakeDoctest(),
]


def test_unmarked_ocr_tests_are_found() -> None:
    assert unmarked_ocr_tests(cast(list[pytest.Item], ITEMS)) == ["t.py::test_ocr_unmarked"]
    assert unmarked_ocr_tests([]) == []


def test_an_unmarked_ocr_test_stops_the_run() -> None:
    message = r"must be marked @pytest\.mark\.slow: t\.py::test_ocr_unmarked$"
    with pytest.raises(pytest.UsageError, match=message):
        pytest_collection_modifyitems(cast(list[pytest.Item], ITEMS))


def test_marked_ocr_tests_and_other_tests_pass_the_guard() -> None:
    allowed = [item for item in ITEMS if item.nodeid != "t.py::test_ocr_unmarked"]
    pytest_collection_modifyitems(cast(list[pytest.Item], allowed))  # no error
