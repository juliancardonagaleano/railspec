#!/usr/bin/env python3
"""Resolve the effort profile (`.spec/perfiles.yaml`) for a role, gate budget,
or explorer count, and read/write a unit's `perfil` selection.

Local, temporary mechanism (same status as `.spec/MODELO-AGENTES.md`): a
kit-side lever for how much a unit spends producing its work, independent of
`riesgo` (which gates are mandatory). `.spec/perfiles.yaml` is the single
source of modelo/effort per role, gate budget per tier, explorer count per
tier, and the implementer's modelo/effort for `complejo` groups. This module
parses that file with a fixed-schema, stdlib-only reader (no PyYAML — `.spec/
scripts/` convention) and exposes both a CLI and the functions that
`scripts/materialize_claude_agents.py` imports to avoid a second reader of
the same YAML.

The Agent tool accepts `model` per invocation but not `effort` — a subagent
always runs at the effort baked into its own frontmatter. So `resolve`
never returns `effort`: when a profile changes a role's effort (only the
`complejo` implementer lever does, today), the resolved `subagent_type` is a
generated variant (`<rol>-<effort>`) whose frontmatter already carries that
effort; otherwise it is the role's own base name. Callers always pass both
`subagent_type` and `model` to the Agent tool.

CLI:
    effort_profile.py resolve --unit <path> --role <rol> [--complex]
    effort_profile.py resolve --unit <path> --gate --tier <bajo|medio|alto>
    effort_profile.py resolve --unit <path> --explorers --tier <bajo|medio|alto>
    effort_profile.py set <perfil> [--unit <path>]
    effort_profile.py show [--unit <path>]
    effort_profile.py set-file <name>
    effort_profile.py clear-file

`resolve`, `set`, and `show` all resolve the local-overlay precedence
(`--profiles` explícito > `.spec/.perfiles-activo` puntero > `.spec/
perfiles.yaml` versionado — spec.md § Comportamiento contratado) before doing
anything else. `set-file`/`clear-file` operate the puntero itself: a second,
independent axis from `set`'s per-unit `perfil:` (spec.md "Dos ejes
independientes").

Exit codes: 0 success, 1 on any explicit failure (bad schema, unknown role or
profile name, unit selection ambiguous or missing) — never a silent
fallback (AGENTS.md § Uso de herramientas en runtime).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILES_PATH = REPO_ROOT / ".spec" / "perfiles.yaml"

# R9 (`pol-dev-clean-code`) — no unnamed literal for the local-overlay
# puntero's path or the filename pattern it designates. `.spec/.perfiles-
# activo` is one clon-local, gitignored line naming a perfil; that name
# designates `.spec/perfiles.<name>.yaml` (spec.md D-1/D-3/P-3 — a name, not
# an arbitrary path).
ACTIVE_PROFILES_POINTER_PATH = REPO_ROOT / ".spec" / ".perfiles-activo"
PROFILES_OVERLAY_FILENAME_PATTERN = "perfiles.{name}.yaml"

# Same directory as this file — `sdd_retomar.py` lives alongside it and is
# reused here (not reimplemented) for unit discovery/`_estado.yaml` reading.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sdd_retomar as _retomar  # noqa: E402

KNOWN_ROLES = (
    "sdd-critico-cumplimiento",
    "sdd-critico-estructural",
    "sdd-critico-profundo",
    "sdd-especificar-redactor",
    "sdd-explorador",
    "sdd-implementador",
    "sdd-planificar-redactor",
    "sdd-refutador",
    "sdd-tareas-redactor",
)
TIERS = ("bajo", "medio", "alto")
COMPLEX_ROLE = "sdd-implementador"


class ProfileError(Exception):
    """Explicit, user-facing failure — never caught to produce a fallback."""


# --- Fixed-schema YAML subset reader ------------------------------------------------
# Not a general YAML parser: only what `perfiles.yaml` needs — nested block
# mappings by indentation, and one-line flow mappings (`{k: v, k2: v2}`) as
# leaves. `_common.py` has nothing for YAML; this mirrors the indentation
# discipline of `sdd_retomar.py::_read_yaml_simple` at a fixed, known depth.


def _parse_scalar(raw: str) -> object:
    value = raw.strip()
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _parse_flow_mapping(raw: str) -> dict[str, object]:
    inner = raw.strip()
    if not (inner.startswith("{") and inner.endswith("}")):
        raise ProfileError(f"mapeo en línea mal formado: {raw!r}")
    inner = inner[1:-1].strip()
    result: dict[str, object] = {}
    if not inner:
        return result
    for part in inner.split(","):
        if ":" not in part:
            raise ProfileError(f"entrada sin ':' en mapeo en línea: {part!r}")
        key, value = part.split(":", 1)
        result[key.strip()] = _parse_scalar(value)
    return result


_KEY_LINE = re.compile(r"^([A-Za-z0-9_\-]+):\s*(.*)$")


def parse_profiles_text(text: str) -> dict[str, object]:
    """Parse the fixed-depth nested-mapping subset used by `perfiles.yaml`."""
    root: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, root)]
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        match = _KEY_LINE.match(stripped)
        if match is None:
            raise ProfileError(f"línea con formato inesperado: {raw_line!r}")
        key, value = match.group(1), match.group(2).strip()
        if value and not value.startswith("{"):
            hash_pos = value.find(" #")
            if hash_pos != -1:
                value = value[:hash_pos].strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            child: dict[str, object] = {}
            parent[key] = child
            stack.append((indent, child))
        elif value.startswith("{"):
            parent[key] = _parse_flow_mapping(value)
        else:
            parent[key] = _parse_scalar(value)
    return root


def _validate_schema(data: dict[str, object], path: Path) -> None:
    default = data.get("default")
    if not isinstance(default, str):
        raise ProfileError(f"{path}: falta la clave 'default'")
    perfiles = data.get("perfiles")
    if not isinstance(perfiles, dict) or "estandar" not in perfiles:
        raise ProfileError(f"{path}: falta 'perfiles.estandar'")
    if default not in perfiles:
        raise ProfileError(
            f"{path}: 'default' ({default!r}) no está declarado en 'perfiles'"
        )
    estandar = perfiles["estandar"]
    if not isinstance(estandar, dict):
        raise ProfileError(f"{path}: 'perfiles.estandar' no es un mapeo")

    roles = estandar.get("roles")
    if not isinstance(roles, dict):
        raise ProfileError(f"{path}: falta 'perfiles.estandar.roles'")
    missing_roles = [r for r in KNOWN_ROLES if r not in roles]
    if missing_roles:
        raise ProfileError(
            f"{path}: 'perfiles.estandar.roles' no declara {missing_roles}"
        )
    for role in KNOWN_ROLES:
        entry = roles[role]
        if not isinstance(entry, dict) or "modelo" not in entry:
            raise ProfileError(
                f"{path}: 'perfiles.estandar.roles.{role}' sin 'modelo'"
            )

    gate = estandar.get("gate")
    if not isinstance(gate, dict) or any(t not in gate for t in TIERS):
        raise ProfileError(
            f"{path}: falta 'perfiles.estandar.gate' completo (bajo/medio/alto)"
        )
    for tier in TIERS:
        entry = gate[tier]
        if not isinstance(entry, dict):
            raise ProfileError(f"{path}: 'perfiles.estandar.gate.{tier}' no es un mapeo")
        for k in ("criticos", "iteraciones", "adversarial"):
            if k not in entry:
                raise ProfileError(
                    f"{path}: 'perfiles.estandar.gate.{tier}' sin '{k}'"
                )

    exploradores = estandar.get("exploradores")
    if not isinstance(exploradores, dict) or any(t not in exploradores for t in TIERS):
        raise ProfileError(
            f"{path}: falta 'perfiles.estandar.exploradores' completo (bajo/medio/alto)"
        )

    complejo = estandar.get("implementador_complejo")
    if not isinstance(complejo, dict) or "modelo" not in complejo or "effort" not in complejo:
        raise ProfileError(
            f"{path}: falta 'perfiles.estandar.implementador_complejo' completo"
        )

    _validate_profile_overrides(perfiles, path)


_PROFILE_TOP_KEYS = ("roles", "gate", "exploradores", "implementador_complejo")
_ROLE_ENTRY_KEYS = ("modelo", "effort")
_TIER_ENTRY_KEYS = ("criticos", "iteraciones", "adversarial")


def _validate_profile_overrides(
    perfiles: dict[str, object], path: Path, *, skip_estandar: bool = True
) -> None:
    """Validate every non-`estandar' perfil's overrides — `estandar` itself is
    already covered above by the required-keys checks (when `skip_estandar`
    is left `True`, the default `_validate_schema` relies on for a
    standalone, complete file). A typo'd role, an unknown gate/exploradores
    tier, an unknown key inside a role or tier entry, or an unknown top-level
    key on the perfil must fail loudly instead of being silently ignored by
    the merge in `_resolve_pair`/`resolve_gate`/`resolve_explorers`.

    `skip_estandar=False` is for a local-overlay file (`resolve_active_
    profiles`'s `_validate_overlay_structure`): there `estandar` is exactly
    the partial-override shape this function already knows how to check for
    every other perfil, not a complete file, so the assumption behind the
    default no longer holds."""
    for profile_name, profile in perfiles.items():
        if skip_estandar and profile_name == "estandar":
            continue
        if not isinstance(profile, dict):
            raise ProfileError(f"{path}: 'perfiles.{profile_name}' no es un mapeo")
        unknown_top = [k for k in profile if k not in _PROFILE_TOP_KEYS]
        if unknown_top:
            raise ProfileError(
                f"{path}: 'perfiles.{profile_name}' declara claves desconocidas {unknown_top}"
            )

        roles = profile.get("roles")
        if roles is not None:
            if not isinstance(roles, dict):
                raise ProfileError(f"{path}: 'perfiles.{profile_name}.roles' no es un mapeo")
            for role, entry in roles.items():
                if role not in KNOWN_ROLES:
                    raise ProfileError(
                        f"{path}: 'perfiles.{profile_name}.roles' declara un rol "
                        f"desconocido {role!r}"
                    )
                if not isinstance(entry, dict):
                    raise ProfileError(
                        f"{path}: 'perfiles.{profile_name}.roles.{role}' no es un mapeo"
                    )
                unknown = [k for k in entry if k not in _ROLE_ENTRY_KEYS]
                if unknown:
                    raise ProfileError(
                        f"{path}: 'perfiles.{profile_name}.roles.{role}' declara claves "
                        f"desconocidas {unknown}"
                    )

        gate = profile.get("gate")
        if gate is not None:
            if not isinstance(gate, dict):
                raise ProfileError(f"{path}: 'perfiles.{profile_name}.gate' no es un mapeo")
            for tier, entry in gate.items():
                if tier not in TIERS:
                    raise ProfileError(
                        f"{path}: 'perfiles.{profile_name}.gate' declara un tier "
                        f"desconocido {tier!r}"
                    )
                if not isinstance(entry, dict):
                    raise ProfileError(
                        f"{path}: 'perfiles.{profile_name}.gate.{tier}' no es un mapeo"
                    )
                unknown = [k for k in entry if k not in _TIER_ENTRY_KEYS]
                if unknown:
                    raise ProfileError(
                        f"{path}: 'perfiles.{profile_name}.gate.{tier}' declara claves "
                        f"desconocidas {unknown}"
                    )

        exploradores = profile.get("exploradores")
        if exploradores is not None:
            if not isinstance(exploradores, dict):
                raise ProfileError(
                    f"{path}: 'perfiles.{profile_name}.exploradores' no es un mapeo"
                )
            unknown_tiers = [t for t in exploradores if t not in TIERS]
            if unknown_tiers:
                raise ProfileError(
                    f"{path}: 'perfiles.{profile_name}.exploradores' declara tiers "
                    f"desconocidos {unknown_tiers}"
                )

        complejo = profile.get("implementador_complejo")
        if complejo is not None:
            if not isinstance(complejo, dict):
                raise ProfileError(
                    f"{path}: 'perfiles.{profile_name}.implementador_complejo' no es un mapeo"
                )
            unknown = [k for k in complejo if k not in _ROLE_ENTRY_KEYS]
            if unknown:
                raise ProfileError(
                    f"{path}: 'perfiles.{profile_name}.implementador_complejo' declara "
                    f"claves desconocidas {unknown}"
                )


def load_profiles(path: Path) -> dict[str, object]:
    """Load and validate `perfiles.yaml`. Raises `ProfileError`, never falls back."""
    if not path.is_file():
        raise ProfileError(f"{path}: no existe")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"{path}: no se pudo leer ({exc})") from exc
    try:
        data = parse_profiles_text(text)
    except ProfileError as exc:
        raise ProfileError(f"{path}: {exc}") from exc
    _validate_schema(data, path)
    return data


# --- Resolution ----------------------------------------------------------------------


def _profile(profiles: dict[str, object], profile_name: str) -> dict[str, object]:
    perfiles = profiles["perfiles"]
    if profile_name not in perfiles:
        raise ProfileError(f"perfil desconocido: {profile_name!r}")
    return perfiles[profile_name]


def _resolve_pair(
    profiles: dict[str, object],
    profile_name: str,
    role: str,
    *,
    complex_: bool = False,
    baseline_profiles: dict[str, object] | None = None,
) -> tuple[object, object, object]:
    """Returns (modelo, effort, base_effort) merged perfil-over-estandar.

    `base_effort` — the yardstick `effort != base_effort` is measured
    against — is normally read out of `estandar` inside `profiles` itself
    (`baseline_profiles=None`, the default: preserves every existing
    caller's behavior unchanged). Pass `baseline_profiles` (the VERSIONED,
    pre-overlay file) when `profiles` is already a fused overlay result: a
    perfil's own `estandar` can be one of the things the overlay just
    edited, and comparing against it then would compare the moved value
    against itself, making a real deviation compare equal and vanish
    instead of being reported (D-4's silent degradation — the same defect
    class `_validate_variant_guard` guards against for `required_variants`,
    confirmed by reproduction against `resolve_role` itself: an overlay
    setting `perfiles.estandar.roles.sdd-critico-profundo: {effort: low}`
    resolved through `resolve_active_profiles` produced the plain agent
    name, no `-low` suffix, silently). `baseline_profiles` never affects
    which `modelo`/`effort` get returned — only what `base_effort` measures
    the deviation against."""
    baseline = baseline_profiles if baseline_profiles is not None else profiles
    baseline_estandar = baseline["perfiles"]["estandar"]
    estandar = profiles["perfiles"]["estandar"]
    profile = _profile(profiles, profile_name)
    if complex_:
        base_effort = baseline_estandar["roles"][COMPLEX_ROLE].get("effort")
        merged = dict(estandar.get("implementador_complejo", {}))
        merged.update(profile.get("implementador_complejo", {}))
    else:
        base = estandar["roles"].get(role, {})
        base_effort = baseline_estandar["roles"].get(role, {}).get("effort")
        merged = dict(base)
        merged.update(profile.get("roles", {}).get(role, {}))
    return merged.get("modelo"), merged.get("effort"), base_effort


def resolve_role(
    profiles: dict[str, object],
    profile_name: str,
    role: str,
    *,
    complex_: bool = False,
    baseline_profiles: dict[str, object] | None = None,
) -> dict[str, str]:
    if role not in KNOWN_ROLES:
        raise ProfileError(f"rol desconocido: {role!r}")
    if complex_ and role != COMPLEX_ROLE:
        raise ProfileError("--complex solo aplica a sdd-implementador")
    modelo, effort, base_effort = _resolve_pair(
        profiles, profile_name, role, complex_=complex_, baseline_profiles=baseline_profiles
    )
    subagent_type = f"{role}-{effort}" if effort != base_effort else role
    return {"subagent_type": subagent_type, "model": modelo}


def resolve_gate(profiles: dict[str, object], profile_name: str, tier: str) -> dict[str, object]:
    if tier not in TIERS:
        raise ProfileError(f"tier desconocido: {tier!r}")
    estandar_gate = profiles["perfiles"]["estandar"]["gate"][tier]
    profile = _profile(profiles, profile_name)
    merged = dict(estandar_gate)
    merged.update(profile.get("gate", {}).get(tier, {}))
    return merged


def resolve_explorers(profiles: dict[str, object], profile_name: str, tier: str) -> int:
    if tier not in TIERS:
        raise ProfileError(f"tier desconocido: {tier!r}")
    base = profiles["perfiles"]["estandar"]["exploradores"][tier]
    profile = _profile(profiles, profile_name)
    return profile.get("exploradores", {}).get(tier, base)


def required_variants(profiles: dict[str, object]) -> set[tuple[str, object, object]]:
    """`(rol, effort, modelo)` for every pair a non-`estandar` effort requires.

    Data-driven over every declared perfil and the `complejo` lever — not a
    hardcoded single entry — so a future perfil that changes an effort grows
    this set without touching the materializer (plan.md § Decisiones).
    """
    variants: set[tuple[str, object, object]] = set()
    for profile_name in profiles["perfiles"]:
        for role in KNOWN_ROLES:
            modelo, effort, base_effort = _resolve_pair(profiles, profile_name, role)
            if effort != base_effort:
                variants.add((role, effort, modelo))
        modelo, effort, base_effort = _resolve_pair(
            profiles, profile_name, COMPLEX_ROLE, complex_=True
        )
        if effort != base_effort:
            variants.add((COMPLEX_ROLE, effort, modelo))
    return variants


class ResolvedProfiles(NamedTuple):
    """Result of the three-level precedence (`--profiles` explícito > puntero
    `.spec/.perfiles-activo` > `.spec/perfiles.yaml` versionado —
    spec.md § Comportamiento contratado). `overlay_path` is `None` when no
    overlay applied: either an explicit `--profiles` was given — which
    disables the puntero outright, even when it names the same versioned
    path (CA-03) — or no puntero is on disk (CA-01). `baseline_profiles` is
    the VERSIONED file, pre-overlay — the same object as `profiles` when no
    overlay applied. A caller that needs to detect "differs from the
    versioned baseline" (`resolve_role`, via `_resolve_pair`) must pass
    THIS, not `profiles`, as `_resolve_pair`'s `baseline_profiles`:
    otherwise, when the overlay itself edited the value being compared, the
    deviation compares equal to itself and disappears (see `_resolve_pair`'s
    docstring — the defect this field exists to let callers avoid)."""

    profiles: dict[str, object]
    overlay_path: Path | None
    overridden_profile_names: tuple[str, ...]
    new_profile_names: tuple[str, ...]
    baseline_profiles: dict[str, object]


_OVERLAY_TOP_KEYS = ("default", "perfiles")


def _profiles_overlay_path(name: str) -> Path:
    return ACTIVE_PROFILES_POINTER_PATH.parent / PROFILES_OVERLAY_FILENAME_PATTERN.format(name=name)


# P-3 (spec.md § Comportamiento contratado) — the puntero stores a NAME, not
# a path: "no se admite ruta arbitraria". Restricting the charset to plain
# filename characters rejects `/`, `\`, `..`, and any other path-shaped
# input by construction, rather than trying to blocklist each dangerous
# case individually.
_OVERLAY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_overlay_name(name: str, *, source: str) -> None:
    """Reject anything that isn't a plain filename component for the local
    overlay's name (P-3): no `/`, no `\\`, no `..`, no empty string.
    `source` names where the invalid value came from (`set-file`'s argument,
    or the puntero file on disk) so the error is actionable either way.
    Called from `_cmd_set_file` (validating the candidate before it is ever
    persisted) and, in depth, from `_read_active_profiles_pointer` (a name
    could reach the puntero file by a means other than `set-file`, e.g. a
    hand-edited file)."""
    if not _OVERLAY_NAME_PATTERN.fullmatch(name):
        raise ProfileError(
            f"{source}: {name!r} no es un nombre de archivo válido (P-3 — el archivo de "
            "perfiles local se designa por NOMBRE, no se admite ruta arbitraria)"
        )


def _read_active_profiles_pointer() -> str | None:
    """Read and validate `.spec/.perfiles-activo`. `None` if absent — that is
    not an error (CA-01/D-1): it means "work with the versioned file". Raises
    `ProfileError` unless the file holds exactly one non-blank name once
    every line is trimmed of spaces/tabs (CA-09): a trailing blank line, the
    kind any editor leaves on save, doesn't count as a second line with
    content, but two actual names — or zero — do. Also raises `ProfileError`
    (P-3, defense in depth — `_cmd_set_file` already validates before
    writing, but this file could have been hand-edited) if that single name
    is not a plain filename component."""
    if not ACTIVE_PROFILES_POINTER_PATH.is_file():
        return None
    try:
        raw_lines = ACTIVE_PROFILES_POINTER_PATH.read_text(encoding="utf-8").split("\n")
    except OSError as exc:
        raise ProfileError(f"{ACTIVE_PROFILES_POINTER_PATH}: no se pudo leer ({exc})") from exc
    content_lines = [line.strip(" \t") for line in raw_lines if line.strip(" \t") != ""]
    if len(content_lines) != 1:
        raise ProfileError(
            f"{ACTIVE_PROFILES_POINTER_PATH}: debe contener un único nombre de perfil "
            "en una sola línea"
        )
    name = content_lines[0]
    _validate_overlay_name(name, source=str(ACTIVE_PROFILES_POINTER_PATH))
    return name


def _validate_overlay_structure(overlay_data: dict[str, object], path: Path) -> dict[str, object]:
    """Structural validation of a local-overlay file: known top-level keys,
    then the same claves/roles/tiers checks `_validate_profile_overrides`
    applies to a perfil's overrides — but *including* `estandar`, which that
    function skips by default on the assumption `estandar` already arrived as
    a complete, validated file (`:213-214` in a standalone `perfiles.yaml`).
    An overlay's `estandar` is exactly the partial-override shape already
    validated for every other perfil, so this passes `skip_estandar=False`
    instead of special-casing `estandar` a second time. Returns the overlay's
    `perfiles` mapping (`{}` when the overlay only carries `default:`,
    CA-07)."""
    unknown_top = [k for k in overlay_data if k not in _OVERLAY_TOP_KEYS]
    if unknown_top:
        raise ProfileError(f"{path}: declara claves de nivel superior desconocidas {unknown_top}")
    overlay_perfiles = overlay_data.get("perfiles", {})
    if not isinstance(overlay_perfiles, dict):
        raise ProfileError(f"{path}: 'perfiles' no es un mapeo")
    _validate_profile_overrides(overlay_perfiles, path, skip_estandar=False)
    return overlay_perfiles


def _merge_one_level(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    """Merge two flat mappings: the overlay's value for a key replaces the
    base's. Used for `exploradores.<tier>` (scalar per tier) and for
    `implementador_complejo` (one attribute mapping)."""
    merged = dict(base)
    merged.update(overlay)
    return merged


def _merge_key_by_key(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    """Merge two mappings-of-mappings one level deep: used for `roles.<rol>`
    and `gate.<tier>` (CA-04, CA-29). The entry each key names is fused
    attribute by attribute, not replaced wholesale — an overlay that
    redeclares only one attribute of an entry keeps the sibling attributes
    the base entry already had."""
    merged = dict(base)
    for key, overlay_value in overlay.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(overlay_value, dict):
            attrs = dict(base_value)
            attrs.update(overlay_value)
            merged[key] = attrs
        else:
            merged[key] = overlay_value
    return merged


def _merge_profile_block(
    base_profile: dict[str, object], overlay_profile: dict[str, object]
) -> dict[str, object]:
    """Merge one perfil's raw block between the two files, at the depth
    plan.md § Decisiones de diseño fixes per attribute:

    - `roles.<rol>` and `gate.<tier>` are mappings of mappings: fused
      key-by-key via `_merge_key_by_key` (CA-04, CA-29) — a `dict.update` at
      the block level would replace an entry wholesale and drop an inherited
      sibling attribute.
    - `exploradores.<tier>` is a scalar per tier (CA-30): the overlay's tier
      value simply replaces the base's, there is no deeper level to merge.
    - `implementador_complejo` is itself one attribute mapping (`modelo`/
      `effort`, like a single role entry): merged one level, same rule as a
      `roles.<rol>` entry.
    """
    merged = dict(base_profile)
    if "roles" in base_profile or "roles" in overlay_profile:
        merged["roles"] = _merge_key_by_key(
            base_profile.get("roles", {}), overlay_profile.get("roles", {})
        )
    if "gate" in base_profile or "gate" in overlay_profile:
        merged["gate"] = _merge_key_by_key(
            base_profile.get("gate", {}), overlay_profile.get("gate", {})
        )
    if "exploradores" in base_profile or "exploradores" in overlay_profile:
        merged["exploradores"] = _merge_one_level(
            base_profile.get("exploradores", {}), overlay_profile.get("exploradores", {})
        )
    if "implementador_complejo" in base_profile or "implementador_complejo" in overlay_profile:
        merged["implementador_complejo"] = _merge_one_level(
            base_profile.get("implementador_complejo", {}),
            overlay_profile.get("implementador_complejo", {}),
        )
    return merged


def _merge_profile_files(
    base_perfiles: dict[str, object], overlay_perfiles: dict[str, object]
) -> dict[str, object]:
    """Merge the two files' `perfiles` mappings, perfil by perfil. A perfil
    the overlay does not name passes through untouched; one it names but the
    base lacks is a brand-new perfil (CA-31), taken as-is.

    Why this runs *before* any perfil→`estandar` inheritance (CA-05): the
    overlay patches the versioned *file*, not a resolution. Fusing here,
    ahead of `_resolve_pair`/`resolve_gate`/`resolve_explorers`, means a
    perfil's own redefinition of a rol keeps its precedence over an overlay
    edit to `estandar` — that inheritance runs downstream, on this already-
    fused dict, oblivious to which file each value came from. Running the two
    fusions in the opposite order would let an overlay edit to `estandar`
    reach a rol the active perfil already redefined, which is exactly the
    inverted-order case CA-05 rejects."""
    merged = dict(base_perfiles)
    for name, overlay_profile in overlay_perfiles.items():
        if name in merged:
            merged[name] = _merge_profile_block(merged[name], overlay_profile)
        else:
            merged[name] = overlay_profile
    return merged


def _diff_overlay_profile_names(
    base_perfiles: dict[str, object], overlay_perfiles: dict[str, object]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Which perfil names the overlay declares override one the versioned
    file already has, vs. are brand-new to it — reported at fijar (CA-20) and
    mostrar (CA-32) so a misspelled name, which produces an inert perfil
    instead of a validation error (spec.md § Comportamiento contratado),
    surfaces in the "nuevos" list rather than passing unnoticed."""
    overridden = tuple(sorted(name for name in overlay_perfiles if name in base_perfiles))
    new = tuple(sorted(name for name in overlay_perfiles if name not in base_perfiles))
    return overridden, new


def _validate_variant_guard(
    base_profiles: dict[str, object], merged_profiles: dict[str, object], overlay_path: Path
) -> None:
    """Every `(rol, effort)` pair the merged result can produce must already
    have an agent variant declared by the VERSIONED file (D-2): the overlay
    may select a modelo/effort combination, never author a new
    `.claude/agents/` variant that only exists on one machine.

    Deliberately does NOT call `required_variants(merged_profiles)`:
    `required_variants`/`_resolve_pair`, called without an explicit
    `baseline_profiles`, compute "differs from the base effort" by reading
    `estandar` out of whichever `profiles` dict they are handed (`plan.md`
    § Reutilización — `_resolve_pair`). Handed the MERGED dict that way,
    that reference point would be the merged `estandar`, not the versioned
    one — so an overlay that edits `perfiles.estandar.roles.<rol>.effort`
    directly would move the very yardstick the comparison uses against that
    same edit, making the edit compare equal to itself and vanish instead of
    raising (D-4's silent degradation, confirmed by reproduction: an overlay
    setting `sdd-planificar-redactor: {effort: xhigh}` under `estandar`
    produced no error and a silently-ignored effort — and, later, the exact
    same defect class reproduced directly against `resolve_role`, not just
    this guard, for `sdd-critico-profundo: {effort: low}`). Passing
    `baseline_profiles=base_profiles` to every `_resolve_pair` call below
    keeps the ground truth fixed at the VERSIONED `estandar` regardless of
    what the overlay touches or which perfil is being iterated."""
    versioned_variants = {(role, effort) for role, effort, _modelo in required_variants(base_profiles)}

    def _require_authorized(role: str, effort: object) -> None:
        if (role, effort) not in versioned_variants:
            variant_name = f"{role}-{effort}"
            raise ProfileError(
                f"{overlay_path}: requiere la variante de agente {variant_name!r}, no "
                f"declarada en {DEFAULT_PROFILES_PATH}"
            )

    for profile_name in merged_profiles["perfiles"]:
        for role in KNOWN_ROLES:
            _modelo, effort, own_profile_base = _resolve_pair(
                merged_profiles, profile_name, role, baseline_profiles=base_profiles
            )
            if effort != own_profile_base:
                _require_authorized(role, effort)
        _modelo_c, effort_c, own_profile_base_c = _resolve_pair(
            merged_profiles, profile_name, COMPLEX_ROLE, complex_=True, baseline_profiles=base_profiles
        )
        if effort_c != own_profile_base_c:
            _require_authorized(COMPLEX_ROLE, effort_c)


def _resolve_overlay(
    overlay_path: Path, base: dict[str, object]
) -> tuple[dict[str, object], tuple[str, ...], tuple[str, ...]]:
    """Fuse `base` (already-loaded, validated versioned profiles) with the
    overlay file at `overlay_path`: structural validation, file merge,
    completeness post-condition, and the agent-variant guard. Shared by
    `resolve_active_profiles` (puntero already pointing here) and `set-file`
    (validating a candidate *before* persisting the puntero — D-4 requires
    the failure to surface now, not on the next resolve)."""
    try:
        overlay_text = overlay_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"{overlay_path}: no se pudo leer ({exc})") from exc
    overlay_data = parse_profiles_text(overlay_text)
    overlay_perfiles = _validate_overlay_structure(overlay_data, overlay_path)

    merged_perfiles = _merge_profile_files(base["perfiles"], overlay_perfiles)
    merged = {
        "default": overlay_data.get("default", base["default"]),
        "perfiles": merged_perfiles,
    }
    # Post-condition (H2): the fusion must still satisfy the same
    # completeness `_validate_schema` requires of a standalone file — this
    # also confirms `default:` names a perfil the fused result declares
    # (CA-11, CA-12).
    _validate_schema(merged, overlay_path)
    _validate_variant_guard(base, merged, overlay_path)

    overridden, new = _diff_overlay_profile_names(base["perfiles"], overlay_perfiles)
    return merged, overridden, new


def resolve_active_profiles(explicit_path: Path | None) -> ResolvedProfiles:
    """Resolve the three-level precedence contracted in spec.md
    § Comportamiento contratado: `--profiles <ruta>` explícito > puntero
    `.spec/.perfiles-activo` > `.spec/perfiles.yaml` versionado. Returns the
    already-fused dict so `resolve_gate`/`resolve_explorers`/
    `required_variants` never need to know an overlay exists — the only three
    callers of this function are the CLI subcommands that resolve a concrete
    run (`_cmd_resolve`, `_cmd_set`, `_cmd_show`); every other consumer of
    `load_profiles` keeps calling it directly and never sees an overlay
    (D-2). Also returns `baseline_profiles` — the versioned file `profiles`
    was fused from (itself, when no overlay applied) — which a caller MUST
    pass on to `resolve_role`'s `baseline_profiles` to get a correct
    `subagent_type` when the active perfil's own edit to `estandar` is what
    changed the effort (see `_resolve_pair`'s docstring)."""
    if explicit_path is not None:
        # CA-03: an explicit `--profiles` replaces the base file outright and
        # disables the puntero, even when it names the same versioned path.
        # This is why the centinela for "flag not passed" cannot be
        # `DEFAULT_PROFILES_PATH` (plan.md § Enfoque) — only `None`
        # distinguishes the two.
        loaded = load_profiles(explicit_path)
        return ResolvedProfiles(
            profiles=loaded,
            overlay_path=None,
            overridden_profile_names=(),
            new_profile_names=(),
            baseline_profiles=loaded,
        )

    base = load_profiles(DEFAULT_PROFILES_PATH)
    pointer_name = _read_active_profiles_pointer()
    if pointer_name is None:
        return ResolvedProfiles(
            profiles=base,
            overlay_path=None,
            overridden_profile_names=(),
            new_profile_names=(),
            baseline_profiles=base,
        )

    overlay_path = _profiles_overlay_path(pointer_name)
    if not overlay_path.is_file():
        raise ProfileError(
            f"{ACTIVE_PROFILES_POINTER_PATH}: apunta a {pointer_name!r}; se esperaba "
            f"{overlay_path}, que no existe"
        )
    merged, overridden, new = _resolve_overlay(overlay_path, base)
    return ResolvedProfiles(
        profiles=merged,
        overlay_path=overlay_path,
        overridden_profile_names=overridden,
        new_profile_names=new,
        baseline_profiles=base,
    )


def authorizes_model(unit_dir: Path, model: str, profiles_path: Path | None = None) -> bool:
    """True if the unit's `perfil` (read from its `_estado.yaml` alone) resolves
    any role, or the `complejo` implementer lever, to `model`.

    This is the sole authorization channel for `0117-D1` (CA-20): a unit with
    `perfil: profundo` and no other key authorizes Fable/Opus because
    `perfiles.yaml > profundo` names them, not because of any other field.
    """
    profiles = load_profiles(profiles_path or DEFAULT_PROFILES_PATH)
    profile_name = _profile_name_for_unit(profiles, str(unit_dir))
    models: set[object] = set()
    for role in KNOWN_ROLES:
        modelo, _effort, _base = _resolve_pair(profiles, profile_name, role)
        models.add(modelo)
    modelo_c, _effort_c, _base_c = _resolve_pair(
        profiles, profile_name, COMPLEX_ROLE, complex_=True
    )
    models.add(modelo_c)
    return model in models


# --- Unit selection (reuses sdd_retomar.py, no second parser/discovery) -------------


def _profile_name_for_unit(profiles: dict[str, object], unit_arg: str | None) -> str:
    if not unit_arg:
        return profiles["default"]
    unit_dir = _retomar._resolver(unit_arg)  # noqa: SLF001 — reused helper, not reimplemented
    if unit_dir is None:
        raise ProfileError(f"no existe la unidad {unit_arg!r}")
    estado_path = unit_dir / "_estado.yaml"
    meta = _retomar._read_yaml_simple(estado_path)  # noqa: SLF001
    name = meta.get("perfil") or profiles["default"]
    if name not in profiles["perfiles"]:
        raise ProfileError(
            f"{estado_path}: perfil declarado {name!r} no existe en perfiles.yaml"
        )
    return name


def _discover_active_unit() -> Path | None:
    """The one non-`done` unit under `.spec/units/`, or None + a printed cause."""
    units = _retomar._listar_unidades()  # noqa: SLF001
    candidates = [u for u in units if u.get("fase") != "done"]
    if len(candidates) == 1:
        return _retomar.UNITS_ROOT / candidates[0]["id"]
    if not candidates:
        print(
            "ERROR: no hay unidad activa bajo .spec/units/ (todas 'done' o no hay "
            "unidades) — usa --unit",
            file=sys.stderr,
        )
    else:
        ids = ", ".join(c["id"] for c in candidates)
        print(
            f"ERROR: hay varias unidades activas ({ids}) — usa --unit para desambiguar",
            file=sys.stderr,
        )
    return None


_PERFIL_LINE = re.compile(r"^perfil:[ \t]*(\S*)([ \t]*#.*)?$", re.MULTILINE)
_RIESGO_LINE = re.compile(r"^riesgo:.*$", re.MULTILINE)


def _write_perfil(estado_path: Path, name: str) -> None:
    """Insert or replace `perfil: <name>` in `_estado.yaml`, byte-preserving
    everything else. Replaces the existing `perfil:` line in place if present
    (keeping any trailing inline comment); otherwise inserts a new line right
    after the top-level `riesgo:` line."""
    text = estado_path.read_text(encoding="utf-8")
    existing = _PERFIL_LINE.search(text)
    if existing is not None:
        comment = existing.group(2) or ""
        text = text[: existing.start()] + f"perfil: {name}{comment}" + text[existing.end():]
    else:
        riesgo = _RIESGO_LINE.search(text)
        if riesgo is None:
            raise ProfileError(f"{estado_path}: no se encontró 'riesgo:' donde insertar 'perfil:'")
        newline_pos = text.find("\n", riesgo.end())
        insertion = f"perfil: {name}\n"
        if newline_pos == -1:
            text = text + "\n" + insertion
        else:
            text = text[: newline_pos + 1] + insertion + text[newline_pos + 1 :]
    estado_path.write_text(text, encoding="utf-8")


# --- CLI -------------------------------------------------------------------------


def _cmd_resolve(args: argparse.Namespace) -> int:
    try:
        resolved = resolve_active_profiles(args.profiles)
        profiles = resolved.profiles
        profile_name = _profile_name_for_unit(profiles, args.unit)
        if args.role:
            if args.gate or args.explorers:
                raise ProfileError("--role es incompatible con --gate/--explorers")
            result: dict[str, object] = resolve_role(
                profiles,
                profile_name,
                args.role,
                complex_=args.complex,
                baseline_profiles=resolved.baseline_profiles,
            )
        elif args.gate:
            if args.complex:
                raise ProfileError("--complex solo aplica junto con --role")
            if not args.tier:
                raise ProfileError("--gate requiere --tier")
            result = resolve_gate(profiles, profile_name, args.tier)
        elif args.explorers:
            if args.complex:
                raise ProfileError("--complex solo aplica junto con --role")
            if not args.tier:
                raise ProfileError("--explorers requiere --tier")
            result = {"explorers": resolve_explorers(profiles, profile_name, args.tier)}
        else:
            raise ProfileError("resolve requiere --role, --gate o --explorers")
    except ProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _cmd_set(args: argparse.Namespace) -> int:
    try:
        profiles = resolve_active_profiles(args.profiles).profiles
    except ProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.name not in profiles["perfiles"]:
        print(f"ERROR: perfil no declarado en perfiles.yaml: {args.name!r}", file=sys.stderr)
        return 1
    if args.unit:
        unit_dir = _retomar._resolver(args.unit)  # noqa: SLF001
        if unit_dir is None:
            print(f"ERROR: no existe la unidad {args.unit!r}", file=sys.stderr)
            return 1
    else:
        unit_dir = _discover_active_unit()
        if unit_dir is None:
            return 1
    estado_path = unit_dir / "_estado.yaml"
    if not estado_path.is_file():
        print(f"ERROR: {estado_path} no existe", file=sys.stderr)
        return 1
    try:
        _write_perfil(estado_path, args.name)
    except ProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OK: perfil={args.name} escrito en {estado_path}")
    return 0


def _full_resolution(
    profiles: dict[str, object],
    profile_name: str,
    *,
    baseline_profiles: dict[str, object] | None = None,
) -> dict[str, object]:
    """`baseline_profiles` doesn't change what this prints: `modelo`/`effort`
    here are the merged values themselves, never the `<rol>-<effort>`
    variant name that depends on comparing against a baseline (that
    comparison only happens in `resolve_role`). Accepted and threaded
    through anyway, for the same reason `_cmd_resolve` takes it — so
    `_resolve_pair`'s `base_effort` return value is correct here too if a
    future field ever surfaces it."""
    roles_out: dict[str, object] = {}
    for role in KNOWN_ROLES:
        modelo, effort, _base = _resolve_pair(
            profiles, profile_name, role, baseline_profiles=baseline_profiles
        )
        entry: dict[str, object] = {"modelo": modelo}
        if effort is not None:
            entry["effort"] = effort
        roles_out[role] = entry
    gate_out = {tier: resolve_gate(profiles, profile_name, tier) for tier in TIERS}
    explorers_out = {tier: resolve_explorers(profiles, profile_name, tier) for tier in TIERS}
    modelo_c, effort_c, _base_c = _resolve_pair(
        profiles, profile_name, COMPLEX_ROLE, complex_=True, baseline_profiles=baseline_profiles
    )
    return {
        "perfil": profile_name,
        "roles": roles_out,
        "gate": gate_out,
        "exploradores": explorers_out,
        "implementador_complejo": {"modelo": modelo_c, "effort": effort_c},
    }


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        resolved = resolve_active_profiles(args.profiles)
        profiles = resolved.profiles
        profile_name = _profile_name_for_unit(profiles, args.unit)
        result = _full_resolution(
            profiles, profile_name, baseline_profiles=resolved.baseline_profiles
        )
        # CA-02/CA-21: name the active file (or its absence) in its own
        # field, so a resolution that depends on a puntero invisible on disk
        # still says where it came from.
        result["archivo_activo"] = (
            str(resolved.overlay_path) if resolved.overlay_path is not None else None
        )
        if resolved.overlay_path is not None:
            # CA-32: perfiles the overlay overrides vs. brand-new ones,
            # reported separately so a misspelled perfil name shows up as
            # "nuevo" instead of passing unnoticed.
            result["perfiles_sobrescritos"] = list(resolved.overridden_profile_names)
            result["perfiles_nuevos"] = list(resolved.new_profile_names)
    except ProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_set_file(args: argparse.Namespace) -> int:
    try:
        # P-3: reject a name shaped like a path (e.g. "x/y") before doing
        # anything else — in particular before the puntero is ever written.
        _validate_overlay_name(args.name, source="set-file")
    except ProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    overlay_path = _profiles_overlay_path(args.name)
    if not overlay_path.is_file():
        print(
            f"ERROR: {overlay_path} no existe — no se fija el archivo activo",
            file=sys.stderr,
        )
        return 1
    try:
        base = load_profiles(DEFAULT_PROFILES_PATH)
        # Dry-run the full resolution against this candidate before
        # persisting the puntero (D-4): a broken overlay must fail now, not
        # the next time something happens to resolve.
        _merged, overridden, new = _resolve_overlay(overlay_path, base)
    except ProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    ACTIVE_PROFILES_POINTER_PATH.write_text(f"{args.name}\n", encoding="utf-8")
    print(f"OK: archivo activo = {overlay_path}")
    print(f"  perfiles sobrescritos: {list(overridden)}")
    print(f"  perfiles nuevos: {list(new)}")
    return 0


def _cmd_clear_file(_args: argparse.Namespace) -> int:
    if ACTIVE_PROFILES_POINTER_PATH.is_file():
        ACTIVE_PROFILES_POINTER_PATH.unlink()
    print("OK: archivo activo limpiado")
    return 0


def main(argv: list[str] | None = None) -> int:
    first_line = __doc__.splitlines()[0] if __doc__ else "effort_profile.py"
    parser = argparse.ArgumentParser(prog="effort_profile.py", description=first_line)
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_profiles_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--profiles", type=Path, default=None,
            help=(
                "Replace the base perfiles.yaml with this path and disable the "
                "local overlay (.spec/.perfiles-activo), even if it points at the "
                "same versioned file. Omit to resolve --profiles > puntero > "
                "versionado."
            ),
        )

    p_resolve = sub.add_parser("resolve", help="Resolve subagent_type/model, gate budget, or explorer count.")
    _add_profiles_arg(p_resolve)
    p_resolve.add_argument("--unit", default=None, help="Unit dir, id, slug prefix, or literal path.")
    p_resolve.add_argument("--role", default=None, choices=KNOWN_ROLES)
    p_resolve.add_argument("--complex", action="store_true", dest="complex")
    p_resolve.add_argument("--gate", action="store_true")
    p_resolve.add_argument("--explorers", action="store_true")
    p_resolve.add_argument("--tier", default=None, choices=TIERS)
    p_resolve.set_defaults(func=_cmd_resolve)

    p_set = sub.add_parser("set", help="Write perfil: <name> into a unit's _estado.yaml.")
    _add_profiles_arg(p_set)
    p_set.add_argument("name")
    p_set.add_argument("--unit", default=None, help="Unit dir, id, or slug prefix. Omit to auto-discover.")
    p_set.set_defaults(func=_cmd_set)

    p_show = sub.add_parser("show", help="Print the fully resolved profile.")
    _add_profiles_arg(p_show)
    p_show.add_argument("--unit", default=None)
    p_show.set_defaults(func=_cmd_show)

    p_set_file = sub.add_parser(
        "set-file",
        help="Select the active local profiles file (.spec/perfiles.<name>.yaml).",
    )
    p_set_file.add_argument("name", help="Local name: designates .spec/perfiles.<name>.yaml.")
    p_set_file.set_defaults(func=_cmd_set_file)

    p_clear_file = sub.add_parser(
        "clear-file",
        help="Clear the active local profiles file (removes .spec/.perfiles-activo).",
    )
    p_clear_file.set_defaults(func=_cmd_clear_file)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
