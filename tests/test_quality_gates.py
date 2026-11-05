"""Guard the CI quality gates against being weakened by accident."""

import tomllib
from pathlib import Path
from typing import Any

import coverage

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
