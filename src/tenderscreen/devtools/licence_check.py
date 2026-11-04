"""Fail when an installed Python distribution declares a GPL, LGPL or AGPL licence.

Run as ``python -m tenderscreen.devtools.licence_check``. It reads only what each
package *declares* in its metadata: the PEP 639 ``License-Expression``, the older
``License`` field and ``License ::`` trove classifiers. It does not scan the text
of bundled licence files, because those routinely quote the GPL in exception
clauses (for example ICU's autoconf scripts inside pypdfium2) without the
package itself being GPL-licensed.

A package that declares no licence at all also fails: we cannot show it is
permissive, so a person has to look at it.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import Distribution, distributions

# GPL, LGPL and AGPL in SPDX ids ("GPL-3.0-only", "LGPL-2.1-or-later"), short
# names ("GPLv3", "AGPLv3+", "GNUGPL"), long names ("GNU Lesser General Public
# License") and common misnomers ("GNU Public License", "GNU Library Public License").
_COPYLEFT = re.compile(
    r"(?<![a-z])(?:gnu)?[al]?gpl"
    r"|gnu\s+(?:\w+\s+)?public\s+licen[cs]e"
    r"|general\s+public\s+licen[cs]e"
    r"|affero",
    re.IGNORECASE,
)

# Placeholders old packaging tools write when no licence was given.
_PLACEHOLDERS = frozenset({"unknown", "none", "n/a", "na", "-"})

# This project does not declare a licence for itself; it is not a dependency.
DEFAULT_IGNORED = frozenset({"tenderscreen"})

_CLASSIFIER_PREFIX = "License ::"


class Problem(StrEnum):
    COPYLEFT = "declares a GPL, LGPL or AGPL licence"
    UNKNOWN = "declares no licence"


@dataclass(frozen=True, slots=True)
class LicenceInfo:
    """The licence metadata one installed distribution declares."""

    name: str
    version: str
    expression: str | None
    licence_field: str | None
    classifiers: tuple[str, ...]

    def declared(self) -> list[str]:
        """Every licence statement, most authoritative first.

        Blanks and placeholders such as "UNKNOWN" (written by old setuptools when
        no licence was given) are dropped: they declare nothing.
        """
        statements = [self.expression, self.licence_field, *self.classifiers]
        stripped = (s.strip() for s in statements if s)
        return [s for s in stripped if s and s.casefold() not in _PLACEHOLDERS]


@dataclass(frozen=True, slots=True)
class Finding:
    info: LicenceInfo
    problem: Problem


def normalise_name(name: str) -> str:
    """PEP 503 normalised project name, so "Foo_Bar" and "foo-bar" match."""
    return re.sub(r"[-_.]+", "-", name).lower()


def is_copyleft(statement: str) -> bool:
    """True if a licence statement names the GPL, LGPL or AGPL."""
    return _COPYLEFT.search(statement) is not None


def licence_classifiers(classifiers: Iterable[str]) -> tuple[str, ...]:
    """Licence trove classifiers with the prefix removed.

    "License :: OSI Approved :: MIT License" becomes "OSI Approved :: MIT License".
    The bare "License :: OSI Approved" names no licence, so it is dropped.
    """
    found = []
    for classifier in classifiers:
        if not classifier.startswith(_CLASSIFIER_PREFIX):
            continue
        rest = classifier.removeprefix(_CLASSIFIER_PREFIX).strip()
        if rest and rest != "OSI Approved":
            found.append(rest)
    return tuple(found)


def info_from_distribution(dist: Distribution) -> LicenceInfo:
    """Read the declared licence of one installed distribution."""
    meta = dist.metadata
    return LicenceInfo(
        name=meta.get("Name") or "<unnamed>",
        version=meta.get("Version") or "<unknown>",
        expression=meta.get("License-Expression"),
        licence_field=meta.get("License"),
        classifiers=licence_classifiers(meta.get_all("Classifier") or []),
    )


def check(infos: Iterable[LicenceInfo], ignored: Iterable[str] = DEFAULT_IGNORED) -> list[Finding]:
    """Return a finding for every distribution that is copyleft or undeclared."""
    skip = {normalise_name(name) for name in ignored}
    findings = []
    for info in infos:
        if normalise_name(info.name) in skip:
            continue
        declared = info.declared()
        if not declared:
            findings.append(Finding(info, Problem.UNKNOWN))
        elif any(is_copyleft(statement) for statement in declared):
            findings.append(Finding(info, Problem.COPYLEFT))
    return sorted(findings, key=lambda f: normalise_name(f.info.name))


def summarise(info: LicenceInfo, limit: int = 70) -> str:
    """One-line licence summary for the report; long licence texts are cut."""
    declared = info.declared()
    if not declared:
        return "(none declared)"
    first_line = declared[0].splitlines()[0]
    return first_line if len(first_line) <= limit else first_line[: limit - 3] + "..."


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tenderscreen.devtools.licence_check",
        description="Fail if any installed distribution declares a GPL, LGPL or AGPL "
        "licence, or declares no licence.",
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="NAME",
        help="skip this distribution after a person has reviewed its licence "
        "(repeatable; record the reason in docs/decisions.md)",
    )
    parser.add_argument(
        "--path",
        action="append",
        help="search these directories for distributions instead of sys.path "
        "(repeatable; used by tests)",
    )
    args = parser.parse_args(argv)

    dists = distributions(path=args.path) if args.path else distributions()
    infos = sorted((info_from_distribution(d) for d in dists), key=lambda i: i.name.lower())
    for info in infos:
        print(f"{info.name:30} {info.version:14} {summarise(info)}")

    allowed = {normalise_name(name) for name in args.allow}
    for info in infos:
        if normalise_name(info.name) in allowed:
            print(f"\nAllowed after review (--allow): {info.name} {info.version}")
    findings = check(infos, ignored=DEFAULT_IGNORED | allowed)
    if not findings:
        print(f"\nOK: {len(infos)} distributions checked; none GPL, LGPL or AGPL.")
        return 0
    print(f"\nFAILED: {len(findings)} distribution(s) need attention:", file=sys.stderr)
    for finding in findings:
        info = finding.info
        print(
            f"  {info.name} {info.version}: {finding.problem.value}: {info.declared()}",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
