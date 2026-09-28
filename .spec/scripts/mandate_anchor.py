#!/usr/bin/env python3
"""Additivity anchor per mandate (unit 0114 — sixth ritual, ancla de aditividad
correction).

`validate-supervised.sh` step 9 (CA-28 of `0109a`) compares `.spec/units/_plan-maestro.md`
against a single, global, frozen file (`base-commit.txt`) to prove the index only ever
gains lines. That anchor never moves forward, so the first legitimate edit to
`_plan-maestro.md` made outside a supervised mandate leaves step 9 red **forever** and
blocks **every future mandate** that runs it. This script gives each mandate its own
anchor instead: the commit its launch actually started from.

Usage
-----
    mandate_anchor.py write  (--plan <id|ruta> | --unidad <ruta>) [--commit <sha>]
                             [--force] [--dry-run]
    mandate_anchor.py resolve (--plan <id|ruta> | --unidad <ruta>)

`--plan`/`--unidad` resolve exactly like `validate_mandate.py`'s own flags of the same
name (`resolve_plan`/`resolve_unit`, imported, never reimplemented): `--unidad` takes a
unit **directory**, not the mandate file directly.

A `## Ancla de aditividad` section is **valid** when it has exactly one line
`commit: <40 hex>`. Any other shape — the section absent, present with no such line,
with a value that is not 40 lowercase hex characters, or with the line repeated —
counts as **absent** for both subcommands (CA-29).

`write` inserts the section as the file's **last** section (D-17) the first time it
creates it from absent: `Mandate.approval_hash()` (`validate_mandate.py:319-326`) only
ever covers the bodies of `## Objetivo y criterio de salida`, `## Unidades miembro` /
`## Unidad amparada`, `## Cadena de dependencias` and `## Delegaciones` — appending
after every existing section is the one position that, by construction, never lands
inside any of those, so a `write` can never change `--hash` and never invalidates an
aprobación a human already signed. When the section is **present but invalid**, `write`
repairs its body in place (exactly one `commit: <40 hex>` line under the same heading,
no duplicated heading, no other section touched) instead of moving it — the heading
already has a fixed position once it exists.

Without `--commit`, the anchored commit is `git rev-parse HEAD`. With `--commit <sha>`
(full or short), it is resolved with `git rev-parse --verify <sha>^{commit}` and always
written normalized to 40 hex — never the short form the caller passed. A `--commit`
that does not resolve to a commit is exit `4` ("commit inválido"); nothing is written.

On a section already valid, `write` does not touch the file and exits `0` (idempotent,
same regime as `append_changelog_line.py`) — **unless** `--force`, which always
replaces it. The only intended caller of `--force` is a human, by hand, relaunching a
mandate after expiry (S-12 of `spec.md`); no script or skill of this unit invokes it
automatically.

`resolve` prints the commit if the section is valid; if it is absent or invalid it
prints nothing and still exits `0` — silence is not an error, it is information for
whoever calls it (typically a shell that decides whether to export `VALIDAR_BASE`).
Only when the mandate/unit does not resolve at all does `resolve` exit non-zero
(D-18): separating "absent" from "invalid" would not change what a caller of `resolve`
needs to know ("is there a value to export or not?"), so both come back silent (KISS).

`--dry-run` on `write` prints, byte for byte, the two lines it would write under the
heading — nothing when the section is already valid and `--force` was not given.

Exit codes
----------
    0  ok — written, repaired, idempotent no-op, or `resolve`'s silence-is-not-error
    1  the mandate/unit does not resolve at all (`resolve_plan`/`resolve_unit` → None)
    2  internal failure (reported as `internal-error: <motivo>`, never a bare traceback)
    4  `write --commit <sha>`: `<sha>` does not resolve to a commit — nothing written

(Code `3` is intentionally unused here, unlike `instance_lock.py`: this script has no
blocking precondition that would need it — kept only for vocabulary consistency across
the six rituals, not because anything needs it.)

stdlib only (plus `git`), no network, no resident process.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import sections  # noqa: E402
from report_git_sync import git, repo_root_of  # noqa: E402
from validate_mandate import resolve_plan, resolve_unit  # noqa: E402

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_INTERNAL = 2
EXIT_BAD_COMMIT = 4

ANCHOR_SECTION = "## Ancla de aditividad"

_COMMIT_LINE = re.compile(r"^commit:[ \t]*(.*)$", re.MULTILINE)
_FULL_HEX = re.compile(r"^[0-9a-f]{40}$")


class AnchorError(Exception):
    """A precondition of this script, carrying the exit code it maps to."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- Mandate resolution (same pair as every other 0114 ritual) ----------------------

def resolve_mandate(plan_arg: str | None, unidad_arg: str | None):
    """Returns the resolved `Mandate`, or `None` (the caller maps that to exit `1`)."""
    if plan_arg:
        return resolve_plan(plan_arg)
    unit_dir = Path(unidad_arg)
    if not unit_dir.is_dir():
        print(f"ERROR — no existe el directorio de unidad: {unit_dir}", file=sys.stderr)
        return None
    return resolve_unit(unit_dir)


# --- Reading and rendering the section -----------------------------------------------

def read_anchor(text: str) -> str | None:
    """The section's commit if — and only if — it is valid (CA-29): the section is
    present and has **exactly one** `commit:` line whose value is 40 lowercase hex
    characters. Zero such lines (heading absent or present with no line), more than
    one (duplicated), or one with a bad value all come back `None` — "absent" and
    "invalid" are the same bucket here, same as `resolve`'s D-18 silence."""
    body = sections(text).get(ANCHOR_SECTION)
    if body is None:
        return None
    values = _COMMIT_LINE.findall(body)
    if len(values) != 1:
        return None
    value = values[0].strip().strip("`")
    return value if _FULL_HEX.match(value) else None


def _find_heading(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if line.rstrip("\n") == ANCHOR_SECTION:
            return index
    return None


def _section_end(lines: list[str], start: int) -> int:
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            return index
    return len(lines)


def render_anchor(text: str, commit: str) -> str:
    """`text` with `## Ancla de aditividad` left as exactly one `commit: <commit>`
    line under its heading.

    Creates the section as the file's **last** section (D-17) when the heading is
    absent. Repairs the body **in place** — without moving the heading or touching any
    other section — when the heading is already there but the body is invalid: CA-29's
    "mismo resultado que crearla desde ausente" names the resulting body (one valid
    `commit:` line), not the position, which a heading that already exists has fixed
    the moment it was written.

    Deliberately **not** built on `instance_lock.replace_section()` nor
    `append_changelog_line._section_bounds()` (D-21): both already solve "replace the
    body of a known section", but they belong to scripts closed before this correction
    existed, and refactoring them to share a helper is a bigger, unrelated change —
    left as a documented duplication, not a hidden one.
    """
    trailing_newline = text.endswith("\n")
    lines = text.splitlines()
    body = [f"commit: {commit}"]
    start = _find_heading(lines)
    if start is None:
        prefix = lines[:]
        if prefix and prefix[-1].strip():
            prefix.append("")
        rebuilt = prefix + [ANCHOR_SECTION, ""] + body
    else:
        end = _section_end(lines, start)
        rebuilt = lines[: start + 1] + [""] + body + [""] + lines[end:]
    result = "\n".join(rebuilt)
    return result + ("\n" if trailing_newline else "")


# --- git subprocesses: same wrapper `report_git_sync.py` already uses ----------------

def resolve_commit(repo_root: Path, commit_arg: str | None) -> str:
    if commit_arg is None:
        return git(repo_root, "rev-parse", "HEAD")
    try:
        return git(repo_root, "rev-parse", "--verify", f"{commit_arg}^{{commit}}")
    except subprocess.CalledProcessError as error:
        raise AnchorError(EXIT_BAD_COMMIT,
                          f"commit inválido: {commit_arg}") from error


# --- Subcommands -----------------------------------------------------------------------

def cmd_write(mandate_path: Path, commit_arg: str | None, force: bool,
             dry_run: bool) -> int:
    text = mandate_path.read_text(encoding="utf-8")
    if read_anchor(text) is not None and not force:
        return EXIT_OK  # already valid, no --force: untouched, nothing printed
    repo_root = repo_root_of(mandate_path)
    commit = resolve_commit(repo_root, commit_arg)
    if dry_run:
        print(f"{ANCHOR_SECTION}\n\ncommit: {commit}")
        return EXIT_OK
    new_text = render_anchor(text, commit)
    if new_text != text:
        mandate_path.write_text(new_text, encoding="utf-8")
    return EXIT_OK


def cmd_resolve(mandate_path: Path) -> int:
    commit = read_anchor(mandate_path.read_text(encoding="utf-8"))
    if commit:
        print(commit)
    return EXIT_OK


# --- CLI -------------------------------------------------------------------------------

def _add_mandate_args(subparser: argparse.ArgumentParser) -> None:
    group = subparser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", metavar="ID|RUTA")
    group.add_argument("--unidad", metavar="RUTA")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mandate_anchor.py",
        description="Ancla de aditividad por mandato (unidad 0114, sexto ritual).")
    subcommands = parser.add_subparsers(dest="subcommand", required=True)

    write = subcommands.add_parser(
        "write", help="crea o repara ## Ancla de aditividad (idempotente)")
    _add_mandate_args(write)
    write.add_argument("--commit", metavar="SHA", default=None,
                       help="por defecto, git rev-parse HEAD")
    write.add_argument("--force", action="store_true",
                       help="reemplaza una sección ya válida (uso humano, S-12)")
    write.add_argument("--dry-run", action="store_true")

    resolve = subcommands.add_parser(
        "resolve", help="imprime el commit si la sección es válida; si no, nada")
    _add_mandate_args(resolve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        mandate = resolve_mandate(args.plan, args.unidad)
        if mandate is None:
            print("ERROR — el mandato/unidad no existe", file=sys.stderr)
            return EXIT_NOT_FOUND
        if args.subcommand == "write":
            return cmd_write(mandate.path, args.commit, args.force, args.dry_run)
        return cmd_resolve(mandate.path)
    except AnchorError as error:
        print(f"{error}", file=sys.stderr)
        return error.code
    except Exception as error:                    # noqa: BLE001 — CA-10 (iv)
        print(f"internal-error: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
