#!/usr/bin/env python3
"""Ritual: validation-with-append (unit 0114-preflight-y-rituales-como-scripts, G2).

Runs `validate_mandate.py` (0109a) as a subprocess, parses its `códigos: …` line
with `codes_from_output()` of `_common.py` — the **same** function the preflight's
validator row uses (`pri-gob-fuente-verdad-unica`: one parser, not two) — and
appends one entry to `validaciones_mandato` of a unit's `_estado.yaml`, in the
exact form `sdd-supervisado` § 9 documents:

    validaciones_mandato:
      - {fecha: "<ISO-8601 UTC>", fase: <fase>, exit: <int>, codigos: [<codes>]}

Usage
-----
    record_mandate_validation.py --unit <dir> --phase <fase> [--mandate <id|ruta>] [--dry-run]

Without `--mandate`: validates the **unit itself** — `validate_mandate.py
--unidad <unit>` — the "unidad aislada" form of `sdd-supervisado` § 1.3 (its
`_estado.yaml > mandato` says what governs it). With `--mandate <id|ruta>`:
validates the **plan mandate** — `validate_mandate.py --plan <mandate>` — the
"plan por objetivo" form; the entry is still appended to `<unit>`'s
`_estado.yaml` (the unit whose retoma the entry documents).

`--dry-run` prints the exact line this script would append — byte-identical to
what a real run writes to `_estado.yaml` (CA-11a) — and writes nothing.

The validator's own exit code is **never** this script's exit code: it travels
inside the appended entry (`exit:`), because a validator FAIL is the expected,
recorded outcome of a ritual run, not a failure of the ritual itself.

Exit codes
----------
    0  ok — entry appended (or, under --dry-run, would have been), regardless
       of the validator's own exit
    1  the unit directory does not exist (no `_estado.yaml`), or — with
       --mandate — the mandate does not resolve to an existing plan
    2  internal-error: <motivo> (unhandled exception)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import codes_from_output  # noqa: E402

VALIDATOR = Path(__file__).resolve().parent / "validate_mandate.py"
# Two levels up from `.spec/scripts/`: the repo root, same self-location basis
# the hooks and `usage/paths.py` use (see `guard_written_state_shape.py`, T13).
REPO_ROOT = Path(__file__).resolve().parents[2]
PLANS_ROOT = REPO_ROOT / ".spec" / "planes"

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_INTERNAL = 2


def _plan_exists(mandate: str) -> bool:
    """Same two forms `validate_mandate.py --plan` resolves: an existing
    directory (fixture, `<dir>/plan.md`) or a plan id under
    `.spec/planes/<id>/plan.md`."""
    p = Path(mandate)
    if p.is_dir():
        return (p / "plan.md").is_file()
    plan_dir = PLANS_ROOT / mandate
    return (plan_dir / "plan.md").is_file()


def entry_line(fecha: str, fase: str, exit_code: int, codes: list[str]) -> str:
    """The exact line appended under `validaciones_mandato:` — also what
    `--dry-run` prints, so the two are byte-identical by construction (CA-11a)."""
    codigos = "[" + ", ".join(codes) + "]"
    return f'  - {{fecha: "{fecha}", fase: {fase}, exit: {exit_code}, codigos: {codigos}}}'


def append_entry(state_path: Path, line: str) -> None:
    """Appends `line` under `validaciones_mandato:` of `state_path`, handling
    both forms the template leaves behind (`_common.py` `field_list()` is what
    locates which form a given `_estado.yaml` is in; the edit itself is a
    direct, minimal text splice — no YAML writer, matching every other script
    of this ritual family)."""
    text = state_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # Form 1: the template's inline empty list — becomes a block with this
    # single new entry.
    for i, raw in enumerate(lines):
        if raw.rstrip("\n") == "validaciones_mandato: []":
            lines[i] = "validaciones_mandato:\n" + line + "\n"
            state_path.write_text("".join(lines), encoding="utf-8")
            return

    # Form 2: already a block — append after the last entry's last line.
    # Entries may be multi-line (normalize_canonical expands flow `- {…}`
    # to a block mapping), so walk every indented body line under the
    # header, not only lines that start with `  - `.
    for i, raw in enumerate(lines):
        if raw.rstrip("\n") == "validaciones_mandato:":
            j = i + 1
            last_content = i
            while j < len(lines):
                nxt = lines[j]
                if nxt.strip():
                    if (len(nxt) - len(nxt.lstrip(" "))) == 0:
                        break
                    last_content = j
                j += 1
            lines.insert(last_content + 1, line + "\n")
            state_path.write_text("".join(lines), encoding="utf-8")
            return

    # Form 3 (defensive fallback, not expected on a unit that already declares
    # `mandato:`): the key is altogether absent — open a fresh block right
    # after `mandato:`, or at the end of the file if that is absent too.
    for i, raw in enumerate(lines):
        if raw.startswith("mandato:"):
            lines.insert(i + 1, "validaciones_mandato:\n" + line + "\n")
            state_path.write_text("".join(lines), encoding="utf-8")
            return
    lines.append("validaciones_mandato:\n" + line + "\n")
    state_path.write_text("".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="record_mandate_validation.py",
        description=(
            "Ritual: corre validate_mandate.py y anexa la entrada a "
            "validaciones_mandato de _estado.yaml (unidad 0114)."
        ),
    )
    ap.add_argument("--unit", required=True, metavar="DIR",
                     help="unidad cuyo _estado.yaml recibe la entrada")
    ap.add_argument("--phase", required=True, metavar="FASE")
    ap.add_argument("--mandate", metavar="ID|RUTA",
                     help="sin esto, valida la unidad misma (--unidad <unit>); "
                          "con esto, valida el plan (--plan <mandate>)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    unit_dir = Path(args.unit)
    state_path = unit_dir / "_estado.yaml"
    if not unit_dir.is_dir() or not state_path.is_file():
        print(f"ERROR — no existe la unidad (o su _estado.yaml): {unit_dir}",
              file=sys.stderr)
        return EXIT_NOT_FOUND

    if args.mandate:
        if not _plan_exists(args.mandate):
            print(f"ERROR — no existe el plan: {args.mandate}", file=sys.stderr)
            return EXIT_NOT_FOUND
        cmd = [sys.executable, str(VALIDATOR), "--plan", args.mandate]
    else:
        cmd = [sys.executable, str(VALIDATOR), "--unidad", str(unit_dir)]

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    codes = codes_from_output(proc.stdout)
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = entry_line(fecha, args.phase, proc.returncode, codes)

    if args.dry_run:
        print(line)
        return EXIT_OK

    append_entry(state_path, line)
    print(line)
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — single catch-all, CA-10 (iv)
        print(f"internal-error: {exc}", file=sys.stderr)
        sys.exit(EXIT_INTERNAL)
