#!/usr/bin/env python3
"""Mechanical coverage count for a `sdd-gate` verdict (unit 0116, CA-12).

Neither subcommand judges anything: they count what the rubric and the
artifact already declare, so the `cobertura` field of the persisted verdict
(`_estado.yaml > gates > <fase>`, step 8 of `panel-convergence-verdict.md`)
never rests on the model's own claim of "I ran N lenses" / "I resolved N
decisions".

Subcommands
-----------
    gate_coverage.py lenses --fase <spec|plan|tasks|codigo> --tier <bajo|medio|alto> [--perfil <ligero|estandar|profundo>]
        Reads the "## Lentes del panel" table of
        `.agents/skills/sdd-gate/references/rubrica-<fase>.md` — never
        hardcoded (plan.md § Decisiones de diseño: the rubric is the single
        source of which lenses exist). Reports how many lenses are active for
        `--tier` (every `**obligatorio**` row, plus every `opcional` row whose
        "Se ejecuta en" cell names `--tier`) over the table's total. Exit 0 on
        success; exit 1 if the rubric file or its table is missing.

        `--perfil`, when given, is accepted and echoed back but never changes
        which lenses are mandatory: the mandatory set depends only on
        `--fase`/`--tier` (`.spec/perfiles.yaml`'s `perfil` axis is how much a
        unit spends producing its work, not which gate lenses are required —
        that is `riesgo`'s axis alone).

    gate_coverage.py decisions --fase <spec|plan|tasks|codigo> [--artefacto <ruta>]
        `--fase spec` (the only phase whose template has a decisions section,
        plan.md § Decisiones de diseño): counts the top-level bullets of
        `spec.md`'s "## Preguntas abiertas" section and how many carry the
        `**resuelto**` marker versus `**abierta**` (the convention this very
        unit's own `spec.md` already uses). Requires `--artefacto` (the
        `spec.md` being evaluated).
        `--fase plan|tasks|codigo`: prints `n/a` and exits 0 — those templates
        have no open/resolved-decisions section, so this deliberately never
        fabricates a zero (`pri-ia-cero-inferencia-implicita`).

No external dependencies: stdlib only (`argparse`, `re`, `sys`, `pathlib`).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import sections  # noqa: E402

DEFAULT_RUBRICS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / ".agents" / "skills" / "sdd-gate" / "references"
)

LENS_SECTION = "## Lentes del panel"
DECISIONS_SECTION = "## Preguntas abiertas"


# --- `lenses` -------------------------------------------------------------------------

def _table_rows(section_body: str) -> tuple[list[str], list[list[str]]] | None:
    """Same minimal pipe-table parser as `derive_tasks.py:_table_rows` — kept
    local rather than imported: it is a 10-line generic helper, not a rule
    this unit owns a single copy of (unlike `_common.py`'s functions)."""
    table_lines = [ln for ln in section_body.splitlines() if ln.strip().startswith("|")]
    if len(table_lines) < 2:
        return None

    def split_row(line: str) -> list[str]:
        inner = line.strip()
        if inner.startswith("|"):
            inner = inner[1:]
        if inner.endswith("|"):
            inner = inner[:-1]
        return [cell.strip() for cell in inner.split("|")]

    header = split_row(table_lines[0])
    data_start = 1
    if re.match(r"^[\s:|-]+$", table_lines[1]):
        data_start = 2
    rows = [split_row(ln) for ln in table_lines[data_start:]]
    return header, rows


def parse_lenses(rubrica_text: str) -> list[dict[str, str]] | None:
    """`[{lente, tipo, se_ejecuta_en}, ...]` from the "## Lentes del panel"
    table; `None` if the section or its table is missing."""
    section = sections(rubrica_text).get(LENS_SECTION)
    if section is None:
        return None
    table = _table_rows(section)
    if table is None:
        return None
    header, rows = table
    normalized = [re.sub(r"\s+", " ", h.strip().lower()) for h in header]
    required = ["lente", "tipo", "se ejecuta en"]
    if not all(name in normalized for name in required):
        return None
    idx = {name: normalized.index(name) for name in required}
    lenses: list[dict[str, str]] = []
    for row in rows:
        if len(row) != len(header) or not row[idx["lente"]]:
            continue
        lenses.append({
            "lente": row[idx["lente"]],
            "tipo": row[idx["tipo"]],
            "se_ejecuta_en": row[idx["se ejecuta en"]],
        })
    return lenses if lenses else None


def is_active(lens: dict[str, str], tier: str) -> bool:
    if "obligatorio" in lens["tipo"].lower():
        return True
    return tier in lens["se_ejecuta_en"].lower() or \
        "todos los tiers" in lens["se_ejecuta_en"].lower()


def cmd_lenses(args: argparse.Namespace) -> int:
    rubricas_dir = Path(args.rubricas_dir) if args.rubricas_dir else DEFAULT_RUBRICS_DIR
    rubrica_path = rubricas_dir / f"rubrica-{args.fase}.md"
    if not rubrica_path.is_file():
        print(f"ERROR — no existe {rubrica_path}", file=sys.stderr)
        return 1
    lenses = parse_lenses(rubrica_path.read_text(encoding="utf-8"))
    if lenses is None:
        print(f"ERROR — {rubrica_path} no tiene una tabla `## Lentes del panel` "
              "reconocible", file=sys.stderr)
        return 1
    active = [lens for lens in lenses if is_active(lens, args.tier)]
    # `--perfil` (CA-12) is accepted and reported back, never used to decide
    # which lenses are active — that decision depends only on fase/tier.
    perfil_suffix = f", perfil {args.perfil}" if args.perfil else ""
    print(f"Lentes activos: {len(active)}/{len(lenses)} (fase {args.fase}, "
          f"tier {args.tier}{perfil_suffix}).")
    for lens in active:
        print(f"  - {lens['lente']} ({lens['tipo']})")
    return 0


# --- `decisions` ------------------------------------------------------------

_TOP_BULLET = re.compile(r"^- .*$")
_RESUELTO = re.compile(r"\*\*resuelto", re.IGNORECASE)


def _top_level_bullets(section: str) -> list[str]:
    """Full text of each level-0 `- ` bullet, continuation lines included —
    the `**resuelto**`/`**abierta**` marker usually sits on the line right
    after the bullet's opening dash, not on it (this unit's own `spec.md` does
    exactly that), so the marker search needs the whole bullet, not just its
    first line."""
    bullets: list[str] = []
    current: list[str] | None = None
    for line in section.splitlines():
        if _TOP_BULLET.match(line):
            if current is not None:
                bullets.append("\n".join(current))
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        bullets.append("\n".join(current))
    return bullets


def parse_decisions(spec_text: str) -> tuple[int, int] | None:
    """`(resueltas, total)` de los bullets de nivel 0 de "## Preguntas
    abiertas"; `None` si la sección no existe."""
    section = sections(spec_text).get(DECISIONS_SECTION)
    if section is None:
        return None
    bullets = _top_level_bullets(section)
    resolved = sum(1 for b in bullets if _RESUELTO.search(b))
    return resolved, len(bullets)


def cmd_decisions(args: argparse.Namespace) -> int:
    if args.fase != "spec":
        print(f"n/a — la plantilla de `{args.fase}` no tiene sección de "
              "decisiones abiertas/resueltas (plan.md § Decisiones de diseño).")
        return 0
    if not args.artefacto:
        print("ERROR — `--fase spec` requiere `--artefacto <spec.md>`", file=sys.stderr)
        return 1
    artefacto = Path(args.artefacto)
    if not artefacto.is_file():
        print(f"ERROR — no existe {artefacto}", file=sys.stderr)
        return 1
    result = parse_decisions(artefacto.read_text(encoding="utf-8"))
    if result is None:
        print(f"n/a — {artefacto} no tiene sección `{DECISIONS_SECTION}`.")
        return 0
    resolved, total = result
    print(f"Decisiones: {resolved}/{total} resueltas ({artefacto}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gate_coverage.py",
        description="Conteo mecánico de lentes/decisiones para el campo "
                     "`cobertura` del veredicto de sdd-gate (CA-12).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    lenses_parser = sub.add_parser(
        "lenses", help="lentes activos de la rúbrica de la fase, según el tier")
    lenses_parser.add_argument("--fase", required=True,
                                choices=["spec", "plan", "tasks", "codigo"])
    lenses_parser.add_argument("--tier", required=True,
                                choices=["bajo", "medio", "alto"])
    lenses_parser.add_argument("--rubricas-dir", metavar="DIR",
                                help="por defecto, .agents/skills/sdd-gate/references")
    lenses_parser.add_argument(
        "--perfil", choices=["ligero", "estandar", "profundo"], default=None,
        help="aceptado y reportado; no afecta qué lentes son obligatorios (CA-12)")
    lenses_parser.set_defaults(func=cmd_lenses)

    decisions_parser = sub.add_parser(
        "decisions", help="decisiones resueltas/abiertas (solo fase spec; n/a en el resto)")
    decisions_parser.add_argument("--fase", required=True,
                                   choices=["spec", "plan", "tasks", "codigo"])
    decisions_parser.add_argument("--artefacto", metavar="RUTA",
                                   help="requerido si --fase spec")
    decisions_parser.set_defaults(func=cmd_decisions)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
