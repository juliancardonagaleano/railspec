#!/usr/bin/env python3
"""Single-instance lock of a supervised mandate (unit 0114 — local, temporary layer).

Mechanizes `## Instancia en curso`, the section that § 1.1 of `sdd-supervisado` writes
when an instance starts and § 10.6 empties when its turn ends. The semantics of the
lock do not change here: this script only stops each conductor from re-deriving the
three bullets by hand.

Usage
-----
    instance_lock.py inspect <mandato>
    instance_lock.py acquire <mandato> --session <id> --launcher <actor>
                     [--start <iso-8601>] [--dry-run]
    instance_lock.py release <mandato> --session <id> [--dry-run]

`<mandato>` is the mandate file: `.spec/planes/<id>/plan.md` for a
goal-based plan, `.spec/units/<NNNN-slug>/mandato.md` for an isolated unit.

**Only `inspect` is read-only** — it is the one the preflight uses, and it is the
whole of what the preflight is allowed to do with the lock. `acquire` and `release`
write the mandate.

`--dry-run` (on `acquire` and `release`) prints, byte for byte, the block it would
write into the section, and touches nothing.

`inspect` prints `libre`, or `ocupado sesion=<…> lanzador=<…> inicio=<…>` with the
literal values of the section.

Exit codes
----------
    0  done (free on `inspect`, written on `acquire`/`release`)
    1  the mandate does not exist, or has no `## Instancia en curso` section
    2  internal failure (reported as `internal-error: <motivo>`, never a bare traceback)
    3  `inspect`/`acquire`: the lock is taken by another session
    4  `release`: the lock was already empty
    5  `release`: the lock belongs to a different `sesion` — nothing is touched
       (freeing another session's orphan lock is the mandate author's act, § 1.1 of
       `sdd-supervisado`, and this script does not decide it)

stdlib only, no network, no resident process.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import bullets, sections  # noqa: E402

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_INTERNAL = 2
EXIT_BUSY = 3
EXIT_ALREADY_FREE = 4
EXIT_OTHER_SESSION = 5

LOCK_SECTION = "## Instancia en curso"

#: Literal written when the section is emptied. The **form** of an empty section is
#: the templates' (`.spec/_plantillas/mandato.md`, § Instancia en
#: curso): a body with no `- sesion:` bullet. The suite compares against the template
#: on that criterion; this constant is only the literal this script leaves behind.
EMPTY_BODY = "Sin instancia."


class LockError(Exception):
    """A precondition of this script, carrying the exit code it maps to."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_mandate(path: Path) -> str:
    if not path.is_file():
        raise LockError(EXIT_NOT_FOUND, f"no existe el mandato: {path}")
    return path.read_text(encoding="utf-8")


def lock_fields(text: str) -> dict[str, str]:
    """`sesion`/`lanzador`/`inicio` of the section. Empty dict when it is free."""
    body = sections(text).get(LOCK_SECTION)
    if body is None:
        raise LockError(EXIT_NOT_FOUND,
                        f"el mandato no tiene sección `{LOCK_SECTION}`")
    fields = bullets(body)
    if not fields.get("sesion", "").strip():
        return {}
    return fields


def replace_section(text: str, body_lines: list[str]) -> str:
    """Rewrites the body of `## Instancia en curso`, leaving every other line intact."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == LOCK_SECTION:
            start = index
            break
    if start is None:
        raise LockError(EXIT_NOT_FOUND,
                        f"el mandato no tiene sección `{LOCK_SECTION}`")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    rebuilt = lines[:start + 1] + [""] + body_lines + [""] + lines[end:]
    return "\n".join(rebuilt) + ("\n" if text.endswith("\n") else "")


def acquire_block(session: str, launcher: str, start: str) -> list[str]:
    """The three bullets of § 1.1, in the order the skill declares them, plus a
    4th `pid` bullet — the parent process id (`os.getppid()`) captured at the
    moment this block is written, not this script's own pid (which exits right
    after writing it). Records who holds the lock, so a later reader can tell
    whether that process is still alive."""
    return [f"- sesion: {session}",
            f"- lanzador: {launcher}",
            f"- inicio: {start}",
            f"- pid: {os.getppid()}"]


def cmd_inspect(path: Path) -> int:
    fields = lock_fields(read_mandate(path))
    if not fields:
        print("libre")
        return EXIT_OK
    print("ocupado sesion={} lanzador={} inicio={}".format(
        fields.get("sesion", ""), fields.get("lanzador", ""),
        fields.get("inicio", "")))
    return EXIT_BUSY


def cmd_acquire(path: Path, session: str, launcher: str, start: str,
                dry_run: bool) -> int:
    text = read_mandate(path)
    fields = lock_fields(text)
    if fields:
        print("ocupado sesion={} lanzador={} inicio={}".format(
            fields.get("sesion", ""), fields.get("lanzador", ""),
            fields.get("inicio", "")), file=sys.stderr)
        return EXIT_BUSY
    block = acquire_block(session, launcher, start)
    print("\n".join(block))
    if not dry_run:
        path.write_text(replace_section(text, block), encoding="utf-8")
    return EXIT_OK


def cmd_release(path: Path, session: str, dry_run: bool) -> int:
    text = read_mandate(path)
    fields = lock_fields(text)
    if not fields:
        print(f"el lock ya estaba vacío: {path}", file=sys.stderr)
        return EXIT_ALREADY_FREE
    owner = fields.get("sesion", "").strip()
    if owner != session:
        print(f"el lock es de otra sesión ({owner}); no se toca nada",
              file=sys.stderr)
        return EXIT_OTHER_SESSION
    print(EMPTY_BODY)
    if not dry_run:
        path.write_text(replace_section(text, [EMPTY_BODY]), encoding="utf-8")
    return EXIT_OK


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="instance_lock.py",
        description="Lock de instancia única de un mandato supervisado (unidad 0114).")
    subcommands = parser.add_subparsers(dest="subcommand", required=True)

    inspect = subcommands.add_parser("inspect", help="solo lectura: libre u ocupado")
    inspect.add_argument("mandate", metavar="MANDATO")

    acquire = subcommands.add_parser("acquire", help="escribe los tres bullets de § 1.1")
    acquire.add_argument("mandate", metavar="MANDATO")
    acquire.add_argument("--session", required=True)
    acquire.add_argument("--launcher", required=True)
    acquire.add_argument("--start", default=None,
                         help="ISO-8601; por defecto, el instante actual en UTC")
    acquire.add_argument("--dry-run", action="store_true")

    release = subcommands.add_parser("release", help="vacía la sección (§ 10.6)")
    release.add_argument("mandate", metavar="MANDATO")
    release.add_argument("--session", required=True)
    release.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.mandate)
    try:
        if args.subcommand == "inspect":
            return cmd_inspect(path)
        if args.subcommand == "acquire":
            return cmd_acquire(path, args.session, args.launcher,
                               args.start or now_iso(), args.dry_run)
        return cmd_release(path, args.session, args.dry_run)
    except LockError as error:
        print(f"{error}", file=sys.stderr)
        return error.code
    except Exception as error:                    # noqa: BLE001 — CA-10 (iv)
        print(f"internal-error: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
