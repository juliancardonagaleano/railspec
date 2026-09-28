#!/usr/bin/env python3
"""Gate iteration budget validator (CA-20).

Compares each phase's `gates.<fase>.iteraciones` in a unit's `_estado.yaml`
against the hard ceiling `.spec/scripts/effort_profile.py` resolves for that
unit's `perfil`/`riesgo` (tier) — the single source of truth for the budget
table (`.spec/perfiles.yaml`). This script imports `load_profiles()` and
`resolve_gate()` from `effort_profile.py` and never re-parses `perfiles.yaml`
on its own (`pol-dev-buscar-antes-de-crear`, same rule `effort_profile.py`
states in its own docstring `:9-13`).

`gates.<fase>.iteraciones` accumulates across every invocation of the gate
for that phase (`sdd-gate/SKILL.md` § Convergencia del gate): the ceiling
this script enforces is a hard cap on that running total, not a per-run
count. There are no "exceptional iterations" — the ceiling is mechanical.

A phase whose last `veredicto` is `escalado` and has no `rehabilitado_por`
only prints a notice on stdout; it does not fail the run. Requiring a human
to record `rehabilitado_por: {por, en, motivo}` before a re-run is a
protocol rule enforced by the humans following `sdd-gate/SKILL.md`, not a
mechanical check this script can perform from the state file alone.

Usage:
    validate_gate_budget.py --unit <path>

Exit codes:
    0 — every phase's `iteraciones` is within its budget (including when
        `gates` is absent or empty)
    1 — at least one phase exceeds its budget, or the unit/profile data
        could not be read
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import child_blocks, field, top_level_block  # noqa: E402
from effort_profile import (  # noqa: E402
    DEFAULT_PROFILES_PATH,
    ProfileError,
    load_profiles,
    resolve_gate,
)

PHASES = ("spec", "plan", "tasks", "codigo")


def _phase_blocks(text: str) -> dict[str, str]:
    """`{fase: block_text}` for each of `PHASES` present under `gates:`.

    Locating `gates:` and its direct children is `_common.top_level_block`/
    `_common.child_blocks` — the same primitive `validate_model_catalog.py`
    uses to locate its own top-level blocks.
    Only the `PHASES` filter is specific to this script.
    """
    section = top_level_block(text, "gates")
    return {key: body for key, body in child_blocks(section) if key in PHASES}


def _block_field(block: str, name: str) -> str:
    """Same convention as `_common.field`, tolerant of the phase block's own
    indentation (its lines are never at column 0)."""
    m = re.search(rf"^[ \t]+{re.escape(name)}:[ \t]*(.*)$", block, re.MULTILINE)
    if not m:
        return ""
    return m.group(1).split("#")[0].strip().strip('"').strip("'")


def validate(unit_dir: Path, profiles_path: Path | None = None) -> int:
    state_file = unit_dir / "_estado.yaml"
    if not state_file.is_file():
        print(f"validate_gate_budget: {state_file}: no existe", file=sys.stderr)
        return 1
    text = state_file.read_text(encoding="utf-8")

    profile_name = field(text, "perfil") or "estandar"
    tier = field(text, "riesgo") or "medio"

    try:
        profiles = load_profiles(profiles_path or DEFAULT_PROFILES_PATH)
        budget = resolve_gate(profiles, profile_name, tier)
    except ProfileError as exc:
        print(f"validate_gate_budget: {exc}", file=sys.stderr)
        return 1

    tope = budget.get("iteraciones")
    if tope is None:
        print(
            f"validate_gate_budget: perfil {profile_name!r} tier {tier!r}: "
            "sin `iteraciones` en el presupuesto resuelto",
            file=sys.stderr,
        )
        return 1

    failed = False
    for fase, block in _phase_blocks(text).items():
        veredicto = _block_field(block, "veredicto")
        rehabilitado = _block_field(block, "rehabilitado_por")
        raw_iter = _block_field(block, "iteraciones")

        # `veredicto`/`rehabilitado_por` is checked independently of whether
        # `iteraciones` is declared — a phase can be `escalado` with no
        # `iteraciones` at all, and that must not skip this check.
        if not raw_iter:
            if veredicto:
                print(
                    f"validate_gate_budget: aviso — iteraciones-ausente {fase}: "
                    f"veredicto={veredicto!r} declarado sin `iteraciones` "
                    "(el techo es mecánico; el contrato exige el campo entero, "
                    "no bloquea esta corrida)"
                )
        else:
            try:
                iteraciones = int(raw_iter)
            except ValueError:
                print(
                    f"validate_gate_budget: {fase}: `iteraciones` no es un entero "
                    f"({raw_iter!r})",
                    file=sys.stderr,
                )
                failed = True
            else:
                if iteraciones > tope:
                    print(
                        f"presupuesto-excedido {fase}: iteraciones={iteraciones} > "
                        f"tope={tope} ({profile_name}, {tier})",
                        file=sys.stderr,
                    )
                    failed = True

        if veredicto == "escalado" and not rehabilitado:
            print(
                f"validate_gate_budget: aviso — {fase}: veredicto=escalado sin "
                "`rehabilitado_por`; una re-corrida exige registrarlo antes "
                "(no bloquea esta corrida)"
            )

    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate gates.<fase>.iteraciones against the unit's profile budget"
    )
    parser.add_argument("--unit", required=True, help="Unit directory (e.g. .spec/units/<id>)")
    parser.add_argument(
        "--profiles", type=Path, default=None, help="Override .spec/perfiles.yaml path (tests)"
    )
    args = parser.parse_args()

    sys.exit(validate(Path(args.unit), args.profiles))


if __name__ == "__main__":
    main()
