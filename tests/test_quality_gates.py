"""Guard the CI quality gates against being weakened by accident."""

import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import coverage
import pytest

from tests.markers import require_slow_marker_on_ocr_tests, unmarked_ocr_tests

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


class FakeItemWithoutFixtures:
    """A collected item with no ``fixturenames`` at all (a plugin's own kind of item)."""

    nodeid = "doc.txt::doc"


ITEMS: list[FakeItem | FakeItemWithoutFixtures] = [
    FakeItem("t.py::test_ocr_marked", ("tmp_path", "tesseract"), slow=True),
    FakeItem("t.py::test_ocr_unmarked", ("tesseract",), slow=False),
    FakeItem("t.py::test_fast", ("tmp_path",), slow=False),
    FakeItem("t.py::test_big_file", ("tmp_path",), slow=True),
    FakeItemWithoutFixtures(),
]


def test_unmarked_ocr_tests_are_found() -> None:
    assert unmarked_ocr_tests(cast(list[pytest.Item], ITEMS)) == ["t.py::test_ocr_unmarked"]
    assert unmarked_ocr_tests([]) == []


def test_an_unmarked_ocr_test_stops_the_run() -> None:
    message = (
        r"give a fake another name\) must be marked @pytest\.mark\.slow: "
        r"t\.py::test_ocr_unmarked$"
    )
    with pytest.raises(pytest.UsageError, match=message):
        require_slow_marker_on_ocr_tests(cast(list[pytest.Item], ITEMS))


def test_marked_ocr_tests_and_other_tests_pass_the_guard() -> None:
    allowed = [item for item in ITEMS if item.nodeid != "t.py::test_ocr_unmarked"]
    require_slow_marker_on_ocr_tests(cast(list[pytest.Item], allowed))  # no error


def run_inner_pytest(folder: Path, ocr_test_marked: bool) -> subprocess.CompletedProcess[str]:
    """A real pytest run, with the guard wired in as in tests/conftest.py."""
    (folder / "pytest.ini").write_text("[pytest]\nmarkers =\n    slow: test\n", encoding="utf-8")
    (folder / "conftest.py").write_text(
        "import pytest\n"
        "from tests.markers import require_slow_marker_on_ocr_tests\n\n\n"
        "@pytest.fixture\n"
        "def tesseract():\n"
        "    return None\n\n\n"
        "def pytest_collection_modifyitems(items):\n"
        "    require_slow_marker_on_ocr_tests(items)\n",
        encoding="utf-8",
    )
    marker = "@pytest.mark.slow\n" if ocr_test_marked else ""
    (folder / "test_inner.py").write_text(
        f"import pytest\n\n\n{marker}def test_ocr(tesseract):\n    pass\n\n\n"
        "def test_fast():\n    pass\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "not slow"],
        cwd=folder,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_guard_stops_a_real_pytest_run(tmp_path: Path) -> None:
    result = run_inner_pytest(tmp_path, ocr_test_marked=False)
    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert "must be marked @pytest.mark.slow: test_inner.py::test_ocr" in result.stderr


def test_marked_ocr_test_is_deselected_in_a_real_fast_run(tmp_path: Path) -> None:
    result = run_inner_pytest(tmp_path, ocr_test_marked=True)
    assert result.returncode == pytest.ExitCode.OK, result.stdout + result.stderr
    assert "1 passed, 1 deselected" in result.stdout
