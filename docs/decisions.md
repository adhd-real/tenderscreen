# Decisions log

Choices made while working unattended, with the reason for each. Newest last.

## 2026-11-04: Setup

### Branch base: built on the unmerged scaffold branch

- **Situation:** `main` holds only an empty initial commit. The project scaffold
  (CLAUDE.md, `pyproject.toml`, `.github/workflows/ci.yml`, hooks) exists only on
  the remote branch `claude/loving-galileo-7um5vl`, which has no pull request.
  Every task refers to that scaffold (CLAUDE.md, `ci.yml`, `src/tenderscreen/`).
- **Chose:** fast-forwarded the working branch `claude/bold-maxwell-8t0jxb` onto
  `claude/loving-galileo-7um5vl` (no history rewritten) and built all tasks on top.
- **Why:** without the scaffold there is no CLAUDE.md to follow and no CI to
  extend. A pull request from this branch to `main` therefore also contains the
  four scaffold commits; if the scaffold branch is merged first, GitHub shows
  only the overnight work.

## Task A: Quality gates

### Coverage gate lives in pyproject.toml and applies to every plain `pytest`

- **Chose:** `--cov` in pytest `addopts`, `[tool.coverage.run] source_pkgs =
  ["tenderscreen"]` and `[tool.coverage.report] fail_under = 90` (line coverage,
  the coverage.py default). CI's existing `pytest` step therefore enforces it; no
  separate threshold is repeated in `ci.yml`.
- **Why:** one source of truth, and the same command passes or fails identically
  in a session and in CI. To run a subset of tests locally without the gate:
  `pytest --no-cov tests/<path>` (added to CLAUDE.md commands).
- **Found in review:** coverage.py rounds the total to `precision` digits before
  comparing with `fail_under`; at the default precision 0, 89.58% rounded to 90%
  and *passed*. Set `precision = 2`. Also set `include_namespace_packages = true`
  so a src/ folder missing `__init__.py` is counted instead of silently skipped.
- `tests/test_quality_gates.py` asks coverage.py for the values it will enforce
  (threshold, precision, source), checks `addopts` has `--cov` and no
  `--no-cov`/`--cov-fail-under`, and that no `.coveragerc`/`setup.cfg`/`tox.ini`
  overrides pyproject.

### Licence check is a small in-repo module, not pip-licenses

- **Chose:** `python -m tenderscreen.devtools.licence_check`, which reads each
  installed distribution's *declared* licence (PEP 639 `License-Expression`,
  `License` field, `License ::` classifiers) and fails on GPL, LGPL or AGPL.
- **Why:** no extra dependency to vet, exact control over matching, and fully
  tested. It deliberately does not grep bundled licence *files*: pypdfium2 ships
  ICU's licence file, which quotes the GPL in autoconf-script exception clauses,
  and LLVM's, which mentions the GPL in its exception text; neither makes
  pypdfium2 GPL (it declares BSD-3-Clause and Apache-2.0).
- **Stricter than asked, on purpose:** a package that declares *no* licence also
  fails (we cannot show it is permissive); so does the placeholder `UNKNOWN`
  that old setuptools writes. Dual licences that offer a GPL option
  (e.g. `MIT OR GPL-2.0`) are flagged too; a person should decide those.
- **Escape hatch for reviewed cases:** `--allow NAME` skips one package. CI passes
  none. Use it only after reading the licence, and record why here. (Possible
  false positive: a package that pastes the full PSF, HPND or MPL text into its
  `License` field, because those texts mention the GNU GPL.)
- Matching also catches misnomers found in review: "GNU Public License",
  "GNU Lesser/Library Public License", "GNUGPL".
- **Noticed, not failing:** `certifi` (via pip-audit → requests) and `pathspec`
  (via mypy) are MPL-2.0. MPL is weak copyleft, not GPL-family, and both are
  dev-tool dependencies only, so the gate passes; listed in the report for you.

### CLAUDE.md edited by adding lines only

- Added two command lines and two layout lines. The sentence "Run all five
  before every commit" was left as is (I may not edit rule text), so it now
  undercounts; you may want to change it to "all of these".
