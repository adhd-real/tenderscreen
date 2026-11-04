import runpy
import sys
from importlib.metadata import distribution, distributions
from pathlib import Path

import pytest

from tenderscreen.devtools.licence_check import (
    Finding,
    LicenceInfo,
    Problem,
    check,
    info_from_distribution,
    is_copyleft,
    licence_classifiers,
    main,
    normalise_name,
    summarise,
)


def make_info(
    name: str = "pkg",
    expression: str | None = None,
    licence_field: str | None = None,
    classifiers: tuple[str, ...] = (),
) -> LicenceInfo:
    return LicenceInfo(name, "1.0", expression, licence_field, classifiers)


def write_dist(site: Path, name: str, headers: list[str]) -> None:
    """Create a minimal installed distribution (dist-info) for the metadata reader."""
    dist_info = site / f"{name}-1.0.dist-info"
    dist_info.mkdir(parents=True)
    lines = ["Metadata-Version: 2.4", f"Name: {name}", "Version: 1.0", *headers]
    (dist_info / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "statement",
    [
        "GPL-3.0-only",
        "GPL-2.0-or-later",
        "LGPL-2.1-or-later",
        "AGPL-3.0-only",
        "MIT OR GPL-2.0",
        "GPLv3",
        "lgplv3+",
        "OSI Approved :: GNU General Public License v3 (GPLv3)",
        "OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)",
        "OSI Approved :: GNU Affero General Public License v3",
        "GNU General Public Licence version 2",
        "GNU Public License v3",
        "GNU Lesser Public License",
        "GNU Library Public License",
        "GNUGPL",
    ],
)
def test_copyleft_statements_are_detected(statement: str) -> None:
    assert is_copyleft(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "MIT",
        "BSD-3-Clause",
        "Apache-2.0 OR BSD-2-Clause",
        "PSF-2.0",
        "ISC",
        "MIT-CMU",
        "MPL-2.0",
        "OSI Approved :: MIT License",
        "Apache License\nVersion 2.0, January 2004",
        "HPND",
        "EPL-2.0",
        "ZPL-2.1",
        "GNU Free Documentation License",
    ],
)
def test_permissive_statements_pass(statement: str) -> None:
    assert not is_copyleft(statement)


def test_normalise_name_follows_pep_503() -> None:
    assert normalise_name("Foo_Bar.baz") == "foo-bar-baz"
    assert normalise_name("tenderscreen") == "tenderscreen"


def test_licence_classifiers_strip_prefix_and_drop_bare_osi() -> None:
    classifiers = [
        "Programming Language :: Python :: 3",
        "License :: OSI Approved",
        "License :: OSI Approved :: MIT License",
    ]
    assert licence_classifiers(classifiers) == ("OSI Approved :: MIT License",)


def test_licence_classifiers_empty() -> None:
    assert licence_classifiers([]) == ()


def test_declared_drops_blank_statements() -> None:
    info = make_info(expression="  ", licence_field="MIT", classifiers=("OSI Approved :: MIT",))
    assert info.declared() == ["MIT", "OSI Approved :: MIT"]


def test_check_flags_copyleft_in_any_field() -> None:
    infos = [
        make_info("good", expression="MIT"),
        make_info("by-expression", expression="GPL-3.0-only"),
        make_info("by-field", licence_field="LGPL"),
        make_info("by-classifier", classifiers=("OSI Approved :: GNU Affero General Public",)),
    ]
    flagged = [(f.info.name, f.problem) for f in check(infos)]
    assert flagged == [
        ("by-classifier", Problem.COPYLEFT),
        ("by-expression", Problem.COPYLEFT),
        ("by-field", Problem.COPYLEFT),
    ]


@pytest.mark.parametrize("placeholder", ["UNKNOWN", "unknown", "None", "N/A", " - "])
def test_placeholder_licence_counts_as_undeclared(placeholder: str) -> None:
    info = make_info("old-setuptools", licence_field=placeholder)
    assert info.declared() == []
    assert check([info]) == [Finding(info, Problem.UNKNOWN)]


def test_check_flags_undeclared_licence() -> None:
    findings = check([make_info("mystery")])
    assert findings == [Finding(make_info("mystery"), Problem.UNKNOWN)]


def test_check_ignores_the_project_itself_by_normalised_name() -> None:
    assert check([make_info("TenderScreen")]) == []
    assert check([make_info("Some_Pkg")], ignored=["some-pkg"]) == []


def test_check_passes_permissive_set() -> None:
    infos = [make_info("a", expression="MIT"), make_info("b", licence_field="BSD")]
    assert check(infos) == []


def test_summarise_variants() -> None:
    assert summarise(make_info()) == "(none declared)"
    assert summarise(make_info(expression="MIT")) == "MIT"
    long_text = "Apache License " + "x" * 200 + "\nsecond line"
    summary = summarise(make_info(licence_field=long_text), limit=20)
    assert summary == "Apache License xx..."
    assert len(summary) == 20


def test_info_from_real_installed_distribution() -> None:
    info = info_from_distribution(distribution("pytest"))
    assert info.name == "pytest"
    assert info.declared()


def test_info_from_distribution_reads_all_fields(tmp_path: Path) -> None:
    write_dist(
        tmp_path,
        "demo",
        [
            "License-Expression: Apache-2.0",
            "License: see file",
            "Classifier: License :: OSI Approved :: Apache Software License",
        ],
    )
    (dist,) = list(distributions(path=[str(tmp_path)]))
    info = info_from_distribution(dist)
    assert info == LicenceInfo(
        "demo", "1.0", "Apache-2.0", "see file", ("OSI Approved :: Apache Software License",)
    )


def test_info_from_distribution_without_metadata_file(tmp_path: Path) -> None:
    (tmp_path / "broken-1.0.dist-info").mkdir()
    (dist,) = list(distributions(path=[str(tmp_path)]))
    info = info_from_distribution(dist)
    assert (info.name, info.version, info.declared()) == ("<unnamed>", "<unknown>", [])


def test_main_allow_skips_a_reviewed_package(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_dist(tmp_path, "Dual_Licensed", ["License-Expression: MIT OR GPL-2.0-only"])
    assert main(["--path", str(tmp_path)]) == 1
    capsys.readouterr()
    assert main(["--path", str(tmp_path), "--allow", "dual-licensed"]) == 0
    assert "Allowed after review (--allow): Dual_Licensed 1.0" in capsys.readouterr().out


def test_main_passes_on_permissive_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_dist(tmp_path, "good", ["License-Expression: MIT"])
    assert main(["--path", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "good" in out
    assert "OK: 1 distributions checked" in out


def test_main_fails_on_gpl_and_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_dist(tmp_path, "good", ["License-Expression: MIT"])
    write_dist(tmp_path, "copyleft", ["License-Expression: GPL-3.0-or-later"])
    write_dist(tmp_path, "mystery", [])
    assert main(["--path", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "2 distribution(s) need attention" in err
    assert "copyleft 1.0: declares a GPL, LGPL or AGPL licence" in err
    assert "mystery 1.0: declares no licence" in err


def test_main_checks_this_environment(capsys: pytest.CaptureFixture[str]) -> None:
    """The dev environment itself must stay free of GPL-family licences."""
    assert main([]) == 0
    assert "OK:" in capsys.readouterr().out


@pytest.mark.filterwarnings("ignore:.*found in sys.modules:RuntimeWarning")
def test_module_runs_as_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_dist(tmp_path, "good", ["License-Expression: MIT"])
    monkeypatch.setattr(sys, "argv", ["licence_check", "--path", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("tenderscreen.devtools.licence_check", run_name="__main__")
    assert exc.value.code == 0
