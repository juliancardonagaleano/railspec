#!/usr/bin/env python3
"""Model catalog auditor for `modelo_ejecucion` (CA-13).

Reads a unit's `_estado.yaml > modelo_ejecucion` block — the flat phase keys
(`especificar`/`planificar`/`tareas`/`implementar`/…) and the nested
`research.exploradores`/`research.consolidacion` branch (H15, H16) — plus,
best-effort, `gates.<fase>.criticos[].modelo` and `gates.<fase>.refutador[]
.modelo` when present, and reports every declared `modelo` that does not
belong to the catalog of models declared across **all** profiles of
`.spec/perfiles.yaml` (not just the unit's own `perfil`).

Builds that catalog from `load_profiles()`, `KNOWN_ROLES`, `COMPLEX_ROLE` and
`_resolve_pair()` of `.spec/scripts/effort_profile.py` — the single reader of
`perfiles.yaml` (its own docstring `:9-13` forbids a second one) — iterating
every profile name it declares, not only the unit's. This script never
touches `effort_profile.py` itself.

This is a **report-only** auditor (P-2 of the unit that added it): it never
blocks a unit for using a model outside the catalog — e.g. the session model
that runs the inline `research.md` consolidation is deliberately whatever
model the conductor happens to run on, not a role `perfiles.yaml` assigns.

Usage:
    validate_model_catalog.py --unit <path> [--profiles <path>]

Exit codes:
    0 — always, once the unit and `perfiles.yaml` were read successfully
        (with or without a `WARN` printed to stdout) — P-2.
    1 — usage error only: `_estado.yaml` missing/unreadable, or
        `perfiles.yaml` missing/malformed.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import field, top_level_block  # noqa: E402
from effort_profile import (  # noqa: E402
    COMPLEX_ROLE,
    DEFAULT_PROFILES_PATH,
    KNOWN_ROLES,
    ProfileError,
    _resolve_pair,
    load_profiles,
)

_KEY_LINE = re.compile(r"^([ \t]*)([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.*)$")
_FLOW_ENTRY = re.compile(r"([A-Za-z_][A-Za-z0-9_]*):\s*((?:\"[^\"]*\")|(?:'[^']*')|[^,}]+)")


def build_catalog(profiles: dict[str, object]) -> set[str]:
    """Set of every `modelo` any declared profile resolves any role to.

    Iterates `profiles["perfiles"]` (every profile name, not just the one a
    given unit selects) so a model authorized under `profundo` but not under
    `estandar` still counts as cataloged.
    """
    models: set[str] = set()
    for profile_name in profiles["perfiles"]:
        for role in KNOWN_ROLES:
            modelo, _effort, _base = _resolve_pair(profiles, profile_name, role)
            if modelo:
                models.add(str(modelo))
        modelo_c, _effort_c, _base_c = _resolve_pair(
            profiles, profile_name, COMPLEX_ROLE, complex_=True
        )
        if modelo_c:
            models.add(str(modelo_c))
    return models


# --- Minimal, local, no-PyYAML walker over a nested `_estado.yaml` block -------------
#
# Handles exactly the three shapes `modelo_ejecucion` and `gates.<fase>` use:
#   - a bare `key:` line with an indented block underneath (dict or list parent)
#   - a `key: {a: b, c: d}` single-line flow mapping (a leaf)
#   - a `- {a: b, c: d}` list item (a leaf inside the list its parent key opened)
# Not a general YAML parser — deliberately local to this script (plan.md §
# G2: the shared list-of-dicts helper another group adds to `_common.py`
# does not fit a *nested* dict like `research.consolidacion`). Finding the
# top-level block to hand it (`modelo_ejecucion:`/`gates:` itself) is
# `_common.top_level_block` — the same primitive `validate_gate_budget.py`
# uses to locate `gates:` before walking its phases (this script used to keep
# its own copy of that lookup).


def _braced(rest: str) -> str | None:
    """`{...}` substring of `rest`, tolerant of a trailing `# comment`. `None`
    when `rest` has no flow mapping."""
    start = rest.find("{")
    end = rest.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return rest[start : end + 1]


def _parse_flow_mapping(braced: str) -> dict[str, str]:
    inner = braced.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    out: dict[str, str] = {}
    for m in _FLOW_ENTRY.finditer(inner):
        value = m.group(2).strip().rstrip(",")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[m.group(1)] = value
    return out


def find_model_entries(block: str, prefix: str) -> list[tuple[str, str]]:
    """`[(dotted_path, modelo_value), ...]` for every `modelo:` reachable
    inside `block`, at any nesting depth, including list items — each path
    prefixed with `prefix` (e.g. `"modelo_ejecucion"`)."""
    results: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []  # (indent, key) of each currently open block
    list_index: dict[int, int] = {}  # indent -> next `- {...}` sibling index

    for line in block.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        stripped = line.strip()

        dash_m = re.match(r"^-[ \t]*(.*)$", stripped)
        if dash_m is not None:
            braced = _braced(dash_m.group(1))
            if braced is not None:
                mapping = _parse_flow_mapping(braced)
                if "modelo" in mapping:
                    path = ".".join([prefix, *(k for _, k in stack)])
                    idx = list_index.get(indent, 0)
                    results.append((f"{path}[{idx}].modelo", mapping["modelo"]))
                list_index[indent] = list_index.get(indent, 0) + 1
            continue

        key_m = _KEY_LINE.match(line)
        if key_m is None:
            continue  # comment line, or anything else this walker does not model
        key = key_m.group(2)
        rest = key_m.group(3).split("#")[0].strip()

        while stack and stack[-1][0] >= indent:
            stack.pop()

        braced = _braced(rest)
        if braced is not None:
            mapping = _parse_flow_mapping(braced)
            path = ".".join([prefix, *(k for _, k in stack), key])
            if "modelo" in mapping:
                results.append((f"{path}.modelo", mapping["modelo"]))
            continue  # a leaf inline dict never opens a child block

        if rest in ("", "[]"):
            # Opening a new child block (dict or list). Any `list_index`
            # counter at a deeper indent belonged to a now-closed sibling —
            # e.g. `criticos` right before `refutador`, or the previous
            # phase's own `criticos` — and must not leak its running count
            # into this one (`refutador[0]` was
            # coming out as `refutador[1]` because the counter was keyed by
            # indent alone, shared across siblings at the same depth).
            # `>=`, not `>`: in compact YAML the `- {...}` items sit at the
            # same column as the key that opens them, so a sibling list at
            # that column must also start its count from zero.
            for stale_indent in [i for i in list_index if i >= indent]:
                del list_index[stale_indent]
            stack.append((indent, key))
        # else: some other scalar field (e.g. `veredicto: refinado`) — ignored

    return results


def collect_declared_models(text: str) -> list[tuple[str, str]]:
    """Every `(dotted_path, modelo)` this script knows how to find in one
    unit's `_estado.yaml`: `modelo_ejecucion.*` (mandatory) plus, best-effort,
    `gates.<fase>.criticos[]`/`gates.<fase>.refutador[]` when present."""
    entries: list[tuple[str, str]] = []
    entries += find_model_entries(top_level_block(text, "modelo_ejecucion"), "modelo_ejecucion")
    entries += find_model_entries(top_level_block(text, "gates"), "gates")
    return entries


def validate(unit_dir: Path, profiles_path: Path | None = None) -> int:
    state_file = unit_dir / "_estado.yaml"
    if not state_file.is_file():
        print(f"validate_model_catalog: {state_file}: no existe", file=sys.stderr)
        return 1
    text = state_file.read_text(encoding="utf-8")
    unit_label = field(text, "id") or unit_dir.name

    try:
        profiles = load_profiles(profiles_path or DEFAULT_PROFILES_PATH)
    except ProfileError as exc:
        print(f"validate_model_catalog: {exc}", file=sys.stderr)
        return 1

    catalog = build_catalog(profiles)

    for dotted_path, modelo in collect_declared_models(text):
        if modelo and modelo not in catalog:
            print(
                f"WARN {unit_label}: {dotted_path}={modelo} no está en el "
                "catálogo de perfiles.yaml"
            )

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Report modelo_ejecucion (and gates.<fase>.criticos/refutador) models "
            "outside the perfiles.yaml catalog — report-only, never blocks (P-2)."
        )
    )
    parser.add_argument("--unit", required=True, help="Unit directory (e.g. .spec/units/<id>)")
    parser.add_argument(
        "--profiles", type=Path, default=None, help="Override .spec/perfiles.yaml path (tests)"
    )
    args = parser.parse_args()

    sys.exit(validate(Path(args.unit), args.profiles))


if __name__ == "__main__":
    main()
