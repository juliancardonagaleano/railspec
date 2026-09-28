#!/usr/bin/env python3
"""Per-artifact size budget validator (unit 0116, local and temporary layer).

Same pattern as `validate_mandate.py:428` `check_sections`: exit != 0 plus a
message that names the rule (the budget) and the real count, stdlib only.

Subcommands
-----------
    validate_artifact_size.py spec <spec.md>
        Rejects (exit 1) a `spec.md` over the `spec` budget in `BUDGETS`
        below (CA-01) — 2000 lines since the 2026-09-28 recalibration.

    validate_artifact_size.py plan <plan.md>
        Rejects a `plan.md` over the `plan` budget in `BUDGETS` below
        (CA-02) — also 2000 lines since that recalibration.

    validate_artifact_size.py skill <SKILL.md>
        Rejects a `SKILL.md` of more than 200 lines. Added by unit 0154
        (CA-06) — orquestador delgado per `pol-ia-no-embeber-conocimiento`.

    validate_artifact_size.py bitacora <bitacora.md>
        Rejects a `bitacora.md` with any level-2 entry (`## <fecha>...` up to
        the next level-2 header, not counting it — a nested level-3 header
        does NOT close the entry) of more than 40 lines (CA-03).

    validate_artifact_size.py plan-maestro <plan-maestro.md>
        Rejects any of the three sections that grow on every retoma —
        `## Paradas`, `## Punto de retoma`, `## Registro de decisiones` — when
        a single level-3 entry (`### <id>`, the per-decision or per-stop
        block) exceeds 60 lines (CA-13 of unit 0123). The validator looks
        only at those three sections: each one is scanned for its `### `
        headers, and a level-3 entry is "lines from the `### ` line up to but
        not counting the next `### ` or `## `" — same rule as `bitacora`
        but starting at level 3. Sections that don't exist are skipped, not
        reported as failures.

    validate_artifact_size.py budget-coverage --root <dir> --gate <veredicto>
        Measures, over the units under `--root` whose `gates.codigo.veredicto`
        equals `--gate` (i.e. already closed and approved), what fraction
        comply with the three budgets above. Exits 1 if coverage is below 90%
        (CR-5) — CA-06 measures and documents, it never recalibrates the
        budgets on its own (plan.md § Decisiones de diseño).

CA-04 (citing an external transcription id instead of copying it) is a fixture
control, not a separate code path: a citing entry is short by construction, so
it naturally passes the line budget above — no semantic copy-detection is
implemented (plan.md § Decisiones de diseño, D3: explicit non-goal).

No external dependencies: stdlib only (`argparse`, `re`, `sys`, `pathlib`).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    FAILURES,
    add_failure,
    codes_from,
    reset,
    units,
)
from unit_state import gate_verdict  # noqa: E402

# Recalibrado 2026-09-21 (0116-D2, decisión directa de Julian): la medición
# real de CA-06 sobre las 19 unidades cerradas con gate `aprobado`/`refinado`
# no cubría el 90% con 300/250/40 (0/19). 450/430/360 cubre 18/19 (94.7%),
# con `0113-medicion-de-consumo-por-sesion` como única excepción documentada
# (bitácora de 1178 líneas, un volcado de datos, no prosa típica).
#
# Recalibrado de nuevo 2026-09-28, decisión directa de Julian: `spec.md` y
# `plan.md` pasan a 2000 líneas, **de aquí en adelante**. Los 450/430
# anteriores dejaban fuera de presupuesto a una especificación que crecía por
# precisión —un criterio por sitio de llamada, con archivo, línea y función
# envolvente—, y la única forma de cumplirlos era partir la unidad o comprimir
# esos criterios. El techo es un límite de legibilidad, no una restricción
# sobre el alcance de una unidad: cuando aprieta contra una especificación
# legítimamente grande, se ajusta el techo. El techo nuevo no reabre ninguna
# decisión ya tomada bajo el anterior: lo que se partió o se acotó para
# cumplirlo se queda como está, y el margen es para las unidades que vienen.
# Los presupuestos de `bitacora` (por entrada) y de `skill` no cambian: su
# tamaño responde a otra razón —el log de handoff se lee de un vistazo y la
# skill es un orquestador delgado—.
BUDGETS = {"spec": 2000, "plan": 2000, "bitacora": 5, "skill": 200}
PLAN_MAESTRO_BUDGET = 60
COVERAGE_THRESHOLD = 0.90


# ======================================================================================
# Line counting
# ======================================================================================

def line_count(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def bitacora_entries(text: str) -> list[tuple[str, int]]:
    """`(anchor, total_lines)` per level-2 entry (header line + body), in
    document order, one tuple per occurrence.

    Same split rule as `_common.sections()` (`:148` — split on `## ` without
    closing on a nested `### `, exactly the CA-03 rule) but **not** that
    function itself: `sections()` returns a `dict` keyed by the header text,
    so two entries with the same header (two automated bitacora entries with
    an identical timestamp/label, which happens in practice) collapse into
    one and the first one's line count is silently lost. Budget validation
    needs every entry counted, duplicates included.
    """
    result: list[tuple[str, int]] = []
    current: str | None = None
    body_lines: list[str] = []

    def flush() -> None:
        if current is not None:
            result.append((current, 1 + len(body_lines)))

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            current = line.strip()
            body_lines = []
        elif current is not None:
            body_lines.append(line)
    flush()
    return result


def check_plan_maestro(path: Path) -> int:
    """`plan-maestro`: per-level-3-entry budget on three sections (CA-13)."""
    reset()
    if not path.is_file():
        print(f"ERROR — no existe {path}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8")
    sections = ("## Paradas", "## Punto de retoma", "## Registro de decisiones")
    for section in sections:
        section_re = re.escape(section)
        m = re.search(rf"(?m)^{section_re}\s*$", text)
        if m is None:
            continue
        start = m.end()
        next_section = re.search(r"(?m)^##\s+", text[start:])
        end = start + next_section.start() if next_section else len(text)
        body = text[start:end]
        for entry_header, count in _level3_entries(body):
            if count > PLAN_MAESTRO_BUDGET:
                add_failure(
                    "presupuesto-plan-maestro-excedido",
                    f"{path}  {section} → {entry_header}",
                    f"{count} líneas, excede el presupuesto de "
                    f"{PLAN_MAESTRO_BUDGET} por entrada "
                    f"(`## {{Paradas, Punto de retoma, Registro de decisiones}}`)",
                )
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} entrada(s) de plan-maestro exceden el "
              f"presupuesto de {PLAN_MAESTRO_BUDGET} líneas por entrada:")
        for c, where, detail in FAILURES:
            print(f"  {c}  {where}  — {detail}")
        print("códigos: " + " ".join(codes_from(FAILURES)))
        return 1
    print(f"PASS — {path}: secciones Paradas/Punto de retoma/Registro de "
          "decisiones dentro del presupuesto.")
    return 0


def _level3_entries(text: str) -> list[tuple[str, int]]:
    """`(header, total_lines)` per `### ` entry in the section body."""
    result: list[tuple[str, int]] = []
    current: str | None = None
    body_lines: list[str] = []

    def flush() -> None:
        if current is not None:
            result.append((current, 1 + len(body_lines)))

    for line in text.splitlines():
        if line.startswith("### "):
            flush()
            current = line.strip()
            body_lines = []
        elif current is not None:
            body_lines.append(line)
    flush()
    return result


# ======================================================================================
# Single-artifact checks
# ======================================================================================

def check_simple(kind: str, path: Path) -> int:
    """`spec`/`plan`: whole-file line budget (CA-01/CA-02)."""
    reset()
    if not path.is_file():
        print(f"ERROR — no existe {path}", file=sys.stderr)
        return 1
    budget = BUDGETS[kind]
    text = path.read_text(encoding="utf-8")
    count = line_count(text)
    code = f"presupuesto-{kind}-excedido"
    if count > budget:
        add_failure(code, str(path),
                    f"{count} líneas, excede el presupuesto de {budget} "
                    f"líneas para `{kind}.md`")
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} incumplimiento(s):")
        for c, where, detail in FAILURES:
            print(f"  {c}  {where}  — {detail}")
        print("códigos: " + " ".join(codes_from(FAILURES)))
        return 1
    print(f"PASS — {path}: {count} líneas (presupuesto {budget}).")
    return 0


def check_bitacora(path: Path) -> int:
    """`bitacora`: per-entry line budget (CA-03)."""
    reset()
    if not path.is_file():
        print(f"ERROR — no existe {path}", file=sys.stderr)
        return 1
    budget = BUDGETS["bitacora"]
    text = path.read_text(encoding="utf-8")
    for anchor, count in bitacora_entries(text):
        if count > budget:
            add_failure("presupuesto-bitacora-excedido", f"{path}  {anchor}",
                        f"{count} líneas, excede el presupuesto de {budget} "
                        "líneas por entrada")
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} entrada(s) de bitácora exceden el "
              f"presupuesto de {budget} líneas por entrada:")
        for c, where, detail in FAILURES:
            print(f"  {c}  {where}  — {detail}")
        print("códigos: " + " ".join(codes_from(FAILURES)))
        return 1
    print(f"PASS — {path}: todas las entradas dentro del presupuesto de "
          f"{budget} líneas.")
    return 0


# ======================================================================================
# CA-06 — budget coverage over closed/approved units
# ======================================================================================

# `gate_verdict` (reads `gates.<fase>.veredicto` from an `_estado.yaml`) used
# to be reimplemented here with its own regex/indentation walk, duplicating
# `unit_state.py:gate_verdict` -- same field, same no-PyYAML approach,
# two parsers that could silently diverge if the `gates:` schema ever changed
# (L3 media, gate de código de 0117, 2026-09-21). Imported from there instead
# (see `from unit_state import gate_verdict` above); `unit_state`'s
# version also understands `GATES_ALIASES` (`implement` -> `codigo`), which
# this call site never needed but no longer has to duplicate either.


def unit_compliant(unit_dir: Path) -> tuple[bool, list[str]]:
    """`(compliant, reasons)` of a unit against the three budgets.

    A unit is compliant when every artifact it actually has (`spec.md`,
    `plan.md`, each `bitacora.md` entry) fits its budget. A missing artifact
    is not a violation — some migrated units do not carry all three
    (`.spec/units/_migradas.md`).
    """
    reasons: list[str] = []
    spec_path = unit_dir / "spec.md"
    if spec_path.is_file():
        count = line_count(spec_path.read_text(encoding="utf-8"))
        if count > BUDGETS["spec"]:
            reasons.append(f"spec.md: {count} > {BUDGETS['spec']}")
    plan_path = unit_dir / "plan.md"
    if plan_path.is_file():
        count = line_count(plan_path.read_text(encoding="utf-8"))
        if count > BUDGETS["plan"]:
            reasons.append(f"plan.md: {count} > {BUDGETS['plan']}")
    bitacora_path = unit_dir / "bitacora.md"
    if bitacora_path.is_file():
        text = bitacora_path.read_text(encoding="utf-8")
        for anchor, count in bitacora_entries(text):
            if count > BUDGETS["bitacora"]:
                reasons.append(f"bitacora.md «{anchor}»: {count} > {BUDGETS['bitacora']}")
    return (not reasons), reasons


def cmd_budget_coverage(args: argparse.Namespace) -> int:
    reset()
    root = Path(args.root)
    # "aprobado" cubre ambos veredictos de gate que cierran una unidad sin
    # escalar (`0117-D6`: `refinado` es el veredicto normal de un gate que
    # pasó tras correcciones del panel, no un rechazo) — filtrar solo por el
    # literal `aprobado` deja la muestra casi vacía (1 unidad en todo el
    # repo) y hace la medición de CR-5 no representativa.
    accepted = {"aprobado", "refinado"} if args.gate == "aprobado" else {args.gate}
    candidates = [
        d for d in units(root)
        if (d / "_estado.yaml").is_file()
        and gate_verdict((d / "_estado.yaml").read_text(encoding="utf-8"), "codigo")
            in accepted
    ]
    if not candidates:
        print(f"PASS — 0 unidad(es) bajo {root} con gates.codigo.veredicto="
              f"{args.gate}; no hay nada que medir.")
        return 0
    exceptions: list[tuple[str, list[str]]] = []
    for unit_dir in candidates:
        compliant, reasons = unit_compliant(unit_dir)
        if not compliant:
            exceptions.append((unit_dir.name, reasons))
    total = len(candidates)
    excepted = len(exceptions)
    coverage = (total - excepted) / total
    pct = coverage * 100
    print(f"Cobertura: {total - excepted}/{total} unidad(es) "
          f"({pct:.1f}%) dentro de los presupuestos "
          f"(spec.md<={BUDGETS['spec']}, plan.md<={BUDGETS['plan']}, "
          f"bitacora.md por entrada<={BUDGETS['bitacora']}).")
    for name, reasons in exceptions:
        print(f"  excepción — {name}: " + "; ".join(reasons))
    if coverage < COVERAGE_THRESHOLD:
        add_failure("cobertura-presupuesto-insuficiente", str(root),
                    f"{pct:.1f}% < {COVERAGE_THRESHOLD * 100:.0f}% requerido (CR-5)")
        print(f"FAIL — cobertura {pct:.1f}% por debajo del "
              f"{COVERAGE_THRESHOLD * 100:.0f}% requerido (CR-5). CA-06: los "
              "presupuestos no se recalibran por esto; cae en el mecanismo "
              "estándar de parada de `.spec/PARADAS-SUPERVISADO.md`.")
        print("códigos: " + " ".join(codes_from(FAILURES)))
        return 1
    print(f"PASS — cobertura {pct:.1f}% cumple el {COVERAGE_THRESHOLD * 100:.0f}% "
          "requerido (CR-5).")
    return 0


# ======================================================================================
# CLI
# ======================================================================================

def main(argv: list[str] | None = None) -> int:
    reset()
    ap = argparse.ArgumentParser(
        prog="validate_artifact_size.py",
        description="Validador de presupuesto de tamaño por artefacto SDD (unidad 0116).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    spec_parser = sub.add_parser("spec", help=f"presupuesto de {BUDGETS['spec']} líneas")
    spec_parser.add_argument("archivo", metavar="SPEC.MD")
    spec_parser.set_defaults(func=lambda a: check_simple("spec", Path(a.archivo)))

    plan_parser = sub.add_parser("plan", help=f"presupuesto de {BUDGETS['plan']} líneas")
    plan_parser.add_argument("archivo", metavar="PLAN.MD")
    plan_parser.set_defaults(func=lambda a: check_simple("plan", Path(a.archivo)))

    skill_parser = sub.add_parser("skill", help=f"presupuesto de {BUDGETS['skill']} líneas para SKILL.md")
    skill_parser.add_argument("archivo", metavar="SKILL.MD")
    skill_parser.set_defaults(func=lambda a: check_simple("skill", Path(a.archivo)))

    bitacora_parser = sub.add_parser(
        "bitacora", help=f"presupuesto de {BUDGETS['bitacora']} líneas por entrada")
    bitacora_parser.add_argument("archivo", metavar="BITACORA.MD")
    bitacora_parser.set_defaults(func=lambda a: check_bitacora(Path(a.archivo)))

    plan_maestro_parser = sub.add_parser(
        "plan-maestro",
        help=f"presupuesto de {PLAN_MAESTRO_BUDGET} líneas por entrada "
             "en ## Paradas / ## Punto de retoma / ## Registro de decisiones")
    plan_maestro_parser.add_argument("archivo", metavar="PLAN-MAESTRO.MD")
    plan_maestro_parser.set_defaults(
        func=lambda a: check_plan_maestro(Path(a.archivo)))

    coverage_parser = sub.add_parser(
        "budget-coverage",
        help="mide la cobertura empírica del presupuesto sobre unidades cerradas (CA-06)")
    coverage_parser.add_argument("--root", required=True, metavar="DIR")
    coverage_parser.add_argument("--gate", required=True, metavar="VEREDICTO",
                                  help="valor de `gates.codigo.veredicto` a filtrar, "
                                       "p.ej. `aprobado`")
    coverage_parser.set_defaults(func=cmd_budget_coverage)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
