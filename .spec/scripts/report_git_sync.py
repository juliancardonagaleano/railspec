#!/usr/bin/env python3
"""Git synchronization report against the resume point (unit 0114 — local layer).

The ritual that § 6 (step 4) and § 8 (step 3) of `sdd-supervisado` describe in prose:
read where the working copy actually is (branch and `HEAD`) and hold it against the
entry in force of `## Punto de retoma`, listing what is uncommitted under the trees
this invocation is going to touch.

**It only reads** (S-4): it never commits, never switches branch and never writes the
mandate. It reports; whoever decides on the report is the preflight or the conductor,
which is why both verdicts exit `0`.

Usage
-----
    report_git_sync.py --mandate <ruta> --unit <dir> [--unit <dir> …] [--dry-run]

`--unit` is repeatable and carries the scope of CA-03, the same one the preflight
receives: the unit trees this invocation is going to touch. The mandate file is part
of the scope too. Anything uncommitted outside those trees is listed apart, as
information, and never changes the verdict.

`--dry-run` prints the same report (there is nothing to simulate in a read).

Output
------
    veredicto: coincide | diverge
    retoma: <fecha de la entrada en vigor>
    rama esperada / rama observada
    commit esperado / commit observado
    alcance: <árboles acotados>
    sucias: <una ruta por línea, o `ninguna`>
    informativas: <una ruta por línea, o `ninguna`>

The verdict compares **branch and `HEAD`** against the resume point: `coincide` when
the declared branch is the one checked out and the declared commit is `HEAD` or an
ancestor of it (see `commit_matches` for why the ancestor arm is the artifact's shape,
not laxity). The uncommitted paths are reported next to it, not folded into it: what
to do about them is the preflight's git rule (CA-03), not this report's.

Exit codes
----------
    0  reported (both `coincide` and `diverge`)
    1  the mandate does not exist
    2  internal failure (reported as `internal-error: <motivo>`, never a bare traceback)
    6  there is nothing to compare against: `## Punto de retoma` is absent, or present
       with no entries (a first launch, whose template leaves it empty). That is a
       precondition of the ritual, not a report — hence its own code

stdlib only (plus `git`), no network, no resident process.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import dirty_paths, resume_point  # noqa: E402

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_INTERNAL = 2
EXIT_NO_RESUME_POINT = 6

_HASH = re.compile(r"\b[0-9a-f]{7,40}\b")


class ReportError(Exception):
    """A precondition of this script, carrying the exit code it maps to."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(repo_root), *args],
                               capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def repo_root_of(path: Path) -> Path:
    completed = subprocess.run(
        ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise ReportError(EXIT_INTERNAL,
                          f"{path.parent} no está dentro de un repositorio git")
    return Path(completed.stdout.strip())


def relative_to_root(repo_root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError as error:
        raise ReportError(EXIT_INTERNAL,
                          f"{path} queda fuera del repositorio {repo_root}") from error


def clean(value: str) -> str:
    return (value or "").strip().strip("`").strip()


def branch_matches(expected: str, observed: str) -> bool:
    return bool(expected) and clean(expected) == observed


def commit_matches(repo_root: Path, expected: str, observed: str) -> bool:
    """Whether the working copy still stands where the resume point says.

    A `commits` field is free prose with hashes in it, so the comparison is by token:
    a hash of the entry counts when it **is** `HEAD` or is an ancestor of it. The
    ancestor arm is not laxity, it is the shape of the artifact: a resume point names
    the commit the state was left at, and the commit that *writes* that resume point is
    necessarily later than the hash it records — demanding strict equality would make
    every well-formed mandate report `diverge`. A hash that is not in the history at
    all, or a different branch, still diverges.
    """
    if not observed:
        return False
    for token in _HASH.findall(expected or ""):
        if observed.startswith(token) or token.startswith(observed):
            return True
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", token, "HEAD"],
            capture_output=True, text=True, check=False)
        if completed.returncode == 0:
            return True
    return False


def print_paths(label: str, paths: list[str]) -> None:
    if not paths:
        print(f"{label}: ninguna")
        return
    print(f"{label}:")
    for path in paths:
        print(f"  {path}")


def report(mandate: Path, units: list[Path]) -> int:
    if not mandate.is_file():
        raise ReportError(EXIT_NOT_FOUND, f"no existe el mandato: {mandate}")
    repo_root = repo_root_of(mandate)

    point = resume_point(mandate.read_text(encoding="utf-8"))
    if point is None:
        raise ReportError(
            EXIT_NO_RESUME_POINT,
            f"{mandate}: `## Punto de retoma` ausente o sin entradas — no hay contra "
            "qué comparar")

    observed_branch = git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    observed_commit = git(repo_root, "rev-parse", "HEAD")
    expected_branch = point.get("rama", "")
    expected_commit = point.get("commits", "")

    matches = (branch_matches(expected_branch, observed_branch)
               and commit_matches(repo_root, expected_commit, observed_commit))

    scope = [relative_to_root(repo_root, unit) for unit in units]
    scope.append(relative_to_root(repo_root, mandate))
    scoped, informational = dirty_paths(repo_root, scope)

    print(f"veredicto: {'coincide' if matches else 'diverge'}")
    print(f"retoma: {point.get('fecha', '')}")
    print(f"rama esperada: {clean(expected_branch) or 'sin declarar'}")
    print(f"rama observada: {observed_branch}")
    print(f"commit esperado: {clean(expected_commit) or 'sin declarar'}")
    print(f"commit observado: {observed_commit}")
    print("alcance: " + ", ".join(scope))
    print_paths("sucias", scoped)
    print_paths("informativas", informational)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="report_git_sync.py",
        description="Reporte de sincronización git contra el punto de retoma (0114).")
    parser.add_argument("--mandate", required=True, metavar="RUTA")
    parser.add_argument("--unit", action="append", default=[], metavar="DIR",
                        help="árbol de unidad del alcance (repetible)")
    parser.add_argument("--dry-run", action="store_true",
                        help="mismo reporte: este ritual solo lee")
    args = parser.parse_args(argv)
    try:
        return report(Path(args.mandate), [Path(u) for u in args.unit])
    except ReportError as error:
        print(f"{error}", file=sys.stderr)
        return error.code
    except Exception as error:                    # noqa: BLE001 — CA-10 (iv)
        print(f"internal-error: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
