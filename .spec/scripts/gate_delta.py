#!/usr/bin/env python3
"""Ephemeral per-iteration delta cache for `sdd-gate` (unit 0116, CA-10/CA-11).

The cache lives at `.spec/units/<unidad>/.gate-delta/<fase>.md` — gitignored,
same treatment as `.spec/.usage/` (plan.md § Decisiones de diseño). It is never
the artifact's source of truth and never a persistent record: it exists only
to hand the panel's critics, from iteration 2 on, a diff instead of the whole
artifact again.

Subcommands
-----------
    gate_delta.py rotate --unidad <dir> --fase <spec|plan|tasks|codigo> \
        --artefacto <ruta-al-artefacto-en-evaluacion>
        No cache for `<fase>` under `<unidad>/.gate-delta/` yet → prints
        "ITERACIÓN 1" plus the artifact's full text (CA-11: the first
        iteration always reads the artifact whole, never an error). A cache
        exists → prints a unified diff (`difflib`, stdlib) between the cached
        content and the current one (CA-10). Either way, the cache is then
        rotated to the artifact's current content. Exit 0 in both cases;
        exit 1 only on a real error (the artifact path does not exist).

    gate_delta.py clear --unidad <dir> --fase <spec|plan|tasks|codigo>
        Deletes the cache for `<fase>` if present. Called when the gate
        persists its verdict (step 8), so a later resume of the unit always
        starts a fresh "iteración 1" (plan.md § Decisiones de diseño). Exit 0
        whether or not there was a cache to delete — clearing an already-clear
        cache is not an error.

No external dependencies: stdlib only (`argparse`, `difflib`, `sys`, `pathlib`).
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path


def cache_path(unidad: Path, fase: str) -> Path:
    return unidad / ".gate-delta" / f"{fase}.md"


def cmd_rotate(args: argparse.Namespace) -> int:
    unidad = Path(args.unidad)
    fase = args.fase
    artefacto = Path(args.artefacto)
    if not artefacto.is_file():
        print(f"ERROR — no existe {artefacto}", file=sys.stderr)
        return 1

    current = artefacto.read_text(encoding="utf-8")
    cache_file = cache_path(unidad, fase)

    if not cache_file.is_file():
        print(f"ITERACIÓN 1 — sin caché previa para `{fase}`, texto completo de "
              f"{artefacto}:")
        print()
        print(current)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(current, encoding="utf-8")
        return 0

    previous = cache_file.read_text(encoding="utf-8")
    diff_lines = list(difflib.unified_diff(
        previous.splitlines(keepends=True),
        current.splitlines(keepends=True),
        fromfile=f"{fase} (iteración anterior)",
        tofile=f"{fase} (actual)",
    ))
    print(f"DELTA — diff desde la iteración anterior de `{fase}` "
          f"({artefacto}):")
    print()
    if diff_lines:
        sys.stdout.writelines(diff_lines)
    else:
        print("(sin cambios respecto a la iteración anterior)")
    cache_file.write_text(current, encoding="utf-8")
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    unidad = Path(args.unidad)
    fase = args.fase
    cache_file = cache_path(unidad, fase)
    if cache_file.is_file():
        cache_file.unlink()
        print(f"OK — caché de delta de `{fase}` limpiada en {unidad}.")
    else:
        print(f"OK — no había caché de delta de `{fase}` en {unidad}; nada que "
              "limpiar.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gate_delta.py",
        description="Recorte por delta entre iteraciones del panel de sdd-gate "
                     "(CA-10/CA-11).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    rotate_parser = sub.add_parser(
        "rotate",
        help="texto completo (iteración 1) o diff (iteración > 1), y rota la caché")
    rotate_parser.add_argument("--unidad", required=True, metavar="DIR")
    rotate_parser.add_argument("--fase", required=True,
                                choices=["spec", "plan", "tasks", "codigo"])
    rotate_parser.add_argument("--artefacto", required=True, metavar="RUTA")
    rotate_parser.set_defaults(func=cmd_rotate)

    clear_parser = sub.add_parser(
        "clear", help="limpia la caché de delta (se corre al persistir el veredicto)")
    clear_parser.add_argument("--unidad", required=True, metavar="DIR")
    clear_parser.add_argument("--fase", required=True,
                               choices=["spec", "plan", "tasks", "codigo"])
    clear_parser.set_defaults(func=cmd_clear)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
