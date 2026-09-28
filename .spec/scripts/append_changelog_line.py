#!/usr/bin/env python3
"""Ritual: changelog append (unit 0114-preflight-y-rituales-como-scripts, G2).

Appends **one** line to the end of `## Changelog` of `.spec/units/_plan-maestro.md`
— the single change `sdd-supervisado` § 12 makes to that index under a mandate. No
formatting is added or inferred (KISS): the caller supplies the full bullet text,
this script only places it and only once.

Usage
-----
    append_changelog_line.py --line "<texto>" [--dry-run]
    append_changelog_line.py --line-file <ruta> [--dry-run]
    append_changelog_line.py --line "<texto>" --index <ruta>   # ritual on a
        # different index — testing only; the real ritual never passes this

`--dry-run` prints the exact line it would append and writes nothing.

Idempotent by design (CA-13): if a line equal to the candidate — byte-equal
after `.strip()` — already exists anywhere in `## Changelog`, nothing is
written and the script still exits 0. Two runs with the same line leave a
single occurrence and the file's diff is limited to that one added line.

Exit codes
----------
    0  ok — appended (or already present, or would have under --dry-run)
    1  `.spec/units/_plan-maestro.md` (or `--index`) has no `## Changelog` section
    2  internal-error: <motivo> (unhandled exception)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX = REPO_ROOT / ".spec" / "units" / "_plan-maestro.md"

HEADING = "## Changelog"

EXIT_OK = 0
EXIT_NO_SECTION = 1
EXIT_INTERNAL = 2


def _section_bounds(lines: list[str]) -> tuple[int, int] | None:
    """(start, end) line indices of the `## Changelog` **body** — the line
    right after the heading up to (but excluding) the next top-level `## `
    heading, or the end of the file. `None` if the heading is absent."""
    start = None
    for i, line in enumerate(lines):
        if line.rstrip("\n") == HEADING:
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return start, end


def already_present(lines: list[str], start: int, end: int, candidate: str) -> bool:
    target = candidate.strip()
    return any(line.strip() == target for line in lines[start:end])


def render(index_path: Path, line: str) -> tuple[bool, bool]:
    """Returns `(had_section, was_appended)`. Writes the file only when a
    section is found and the line is not already present."""
    text = index_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    bounds = _section_bounds(lines)
    if bounds is None:
        return False, False
    start, end = bounds

    if already_present(lines, start, end, line):
        return True, False

    insertion = line if line.endswith("\n") else line + "\n"
    # Make sure the line right before the insertion point ends in a newline —
    # true only when `## Changelog` is the file's last section and the file
    # itself has no trailing newline (rare, but cheap to guard).
    if end > start and not lines[end - 1].endswith("\n"):
        lines[end - 1] = lines[end - 1] + "\n"
    lines.insert(end, insertion)
    index_path.write_text("".join(lines), encoding="utf-8")
    return True, True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="append_changelog_line.py",
        description=(
            "Ritual: anexa una línea a ## Changelog de _plan-maestro.md "
            "(unidad 0114), sin duplicar."
        ),
    )
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--line", metavar="TEXTO")
    group.add_argument("--line-file", metavar="RUTA")
    ap.add_argument("--index", metavar="RUTA", default=str(DEFAULT_INDEX),
                     help="ruta del índice a editar (testing; por defecto "
                          ".spec/units/_plan-maestro.md)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    line = (
        args.line
        if args.line is not None
        else Path(args.line_file).read_text(encoding="utf-8").rstrip("\n")
    )

    index_path = Path(args.index)
    if not index_path.is_file():
        print(f"ERROR — no existe el índice: {index_path}", file=sys.stderr)
        return EXIT_NO_SECTION

    if args.dry_run:
        text = index_path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        bounds = _section_bounds(lines)
        if bounds is None:
            print(f"ERROR — {index_path} no tiene sección {HEADING}", file=sys.stderr)
            return EXIT_NO_SECTION
        start, end = bounds
        if not already_present(lines, start, end, line):
            print(line.rstrip("\n"))
        return EXIT_OK

    had_section, appended = render(index_path, line)
    if not had_section:
        print(f"ERROR — {index_path} no tiene sección {HEADING}", file=sys.stderr)
        return EXIT_NO_SECTION
    if appended:
        print(line.rstrip("\n"))
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — single catch-all, CA-10 (iv)
        print(f"internal-error: {exc}", file=sys.stderr)
        sys.exit(EXIT_INTERNAL)
