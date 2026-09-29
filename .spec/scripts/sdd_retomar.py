#!/usr/bin/env python3
"""Resume SDD unit state from disk without launching an agent.

Replaces the heavy `sdd-retomar` agent invocation (which historically cost
~6.4M tokens of output across the four models it could inherit, per
`research.md §2.8`) with a deterministic read of `_estado.yaml`, `tasks.md`
and `bitacora.md`. Emits a JSON report suitable for the calling session to
absorb in <2k tokens.

Exit codes:
  0  Success — unit found, state emitted.
  1  No units under `.spec/units/` matching the hint.
  2  Multiple candidates, no hint given — emit the candidate list and exit.
  3  Hint given but no unit matches.
  4  Unit found but `_estado.yaml` malformed (unreadable). Detail in stderr.

Usage:
    python3 .spec/scripts/sdd_retomar.py                   # list candidates if ambiguous
    python3 .spec/scripts/sdd_retomar.py <id-or-slug>     # resume by hint
    python3 .spec/scripts/sdd_retomar.py <dir-path>       # literal directory (fixture)

Decisión directa de Julian, `0123-D2` (2026-09-21), bajo CR-2:
  - prefijo medido por `0113` baja — el script emite un JSON fijo ≤ 2 k tokens
    en vez de un agente Haiku que cargaba todo el system prompt + SKILL.md.
  - ningún test pasa a rojo — `test_skills_reference_scripts.py` exige que
    `sdd-retomar/SKILL.md` cite un script por ruta; este archivo es ese script.
  - no altera MUST/MUST NOT — el comportamiento de retomar la unidad desde
    `_estado.yaml`/`tasks.md`/`bitacora.md` es el mismo, solo cambia *cómo*
    se computa (script vs agente).

Limitations, deliberately accepted (D-13):
  - does NOT consult `pce-mcp`; the calling session does that if needed.
  - does NOT enumerate `governance_refs` against the catalog (it just lists them).
  - does NOT look at `modelo_ejecucion` audit; the unit's own bitácora does.
  - if the unit is `modo: supervisado`, the conductor (sdd-supervisado) is in
    charge of the validator run, not this script — same boundary as before.

Stdout: a JSON object with one of these shapes:
  {"kind": "candidates", "units": [{"id": "...", "fase": "...", "estado": "..."}, ...]}
  {"kind": "resume", "unit": "...", "fase": "...", "estado": "...", "modo": "...",
   "riesgo": "...", "gates": {...}, "aprobacion": {...}, "next": "...",
   "tasks_pending": ["..."], "last_handoff": "...", "governance_refs": [...],
   "comando_validacion": "..."}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UNITS_ROOT = REPO_ROOT / ".spec" / "units"

PHASE_TO_SKILL = {
    "research": "sdd-especificar",
    "spec": "sdd-planificar",
    "plan": "sdd-tareas",
    "tasks": "sdd-implementar (interactivo) / redactar paquete de aprobación (semi-autonomo)",
    "aprobacion": "presentar el paquete, no implementar",
    "implement": "sdd-implementar",
    "done": "cerrada — nada",
}


def _read_yaml_simple(path: Path) -> dict[str, object]:
    """Tiny regex-based reader for top-level scalars + nested block scalars.

    Does not parse flow mappings (entries like `- {lente: L1, subagente: ...}`)
    because the resume report does not need them — those live in
    `modelo_ejecucion` / `gates.<fase>.criticos`, which are surfaced as raw
    blocks rather than parsed.

    Inline comments after a value (`fase: done  # spec | plan | tasks`) are
    stripped so the report does not echo the template's enumeration comment.
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    out: dict[str, object] = {}
    cur_key: str | None = None
    cur_block: list[str] = []

    def _strip_inline_comment(value: str) -> str:
        """Drop a `# ...` tail that is preceded by whitespace — keep `#` inside."""
        m = re.search(r"\s+#", value)
        if m is not None:
            value = value[: m.start()]
        return value.strip()

    def flush() -> None:
        if cur_key is not None:
            joined = "\n".join(cur_block).rstrip("\n")
            out[cur_key] = _strip_inline_comment(joined)

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        top_match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if top_match is not None and not line.startswith(" ") and not line.startswith("-"):
            flush()
            new_key = top_match.group(1)
            inline = top_match.group(2).strip()
            if inline:
                out[new_key] = _strip_inline_comment(inline).strip('"').strip("'")
                cur_key = None
            else:
                cur_key = new_key
                cur_block = []
            continue
        if cur_key is None:
            continue
        if line.startswith("  "):
            cur_block.append(line[2:])
    flush()
    return out


def _listar_unidades() -> list[dict[str, str]]:
    """`{id, fase, estado}` for each unit directory under `.spec/units/`."""
    units: list[dict[str, str]] = []
    if not UNITS_ROOT.is_dir():
        return units
    for entry in sorted(UNITS_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        estado_path = entry / "_estado.yaml"
        if not estado_path.is_file():
            continue
        meta = _read_yaml_simple(estado_path)
        units.append({
            "id": entry.name,
            "fase": str(meta.get("fase", "?")),
            "estado": str(meta.get("estado", "?")),
        })
    return units


def _resolver(hint: str) -> Path | None:
    """Resolve a hint to a directory under `.spec/units/` (or literal elsewhere)."""
    p = Path(hint)
    if p.is_dir():
        return p
    candidate = UNITS_ROOT / hint
    if candidate.is_dir():
        return candidate
    for entry in UNITS_ROOT.iterdir():
        if entry.is_dir() and entry.name.startswith(hint):
            return entry
    return None


def _tasks_pendientes(tasks_path: Path) -> list[str]:
    if not tasks_path.is_file():
        return []
    lines = tasks_path.read_text(encoding="utf-8").splitlines()
    pendientes: list[str] = []
    for line in lines:
        m = re.match(r"^\s*-\s*\[\s*\]\s*(.+)$", line)
        if m is None:
            continue
        pendientes.append(m.group(1).strip())
        if len(pendientes) >= 10:
            pendientes.append("...")
            break
    return pendientes


def _last_event_line(bitacora_path: Path) -> str:
    """Return only the header line of the latest `## <timestamp>` entry."""
    if not bitacora_path.is_file():
        return ""
    text = bitacora_path.read_text(encoding="utf-8")
    entries = re.split(r"(?m)^##\s+", text)
    if len(entries) < 2:
        return ""
    latest = entries[-1]
    first_line = latest.splitlines()[0] if latest.splitlines() else ""
    return first_line.strip()


def _emit_candidates(units: list[dict[str, str]]) -> None:
    payload = {"kind": "candidates", "units": units}
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _vigencia_gobernanza(meta: dict[str, object]) -> dict[str, str]:
    """Extrae `verificado_en` y `fs_hash` del último gate aprobado.

    Unit 0003 (fix 6) introduce la convención: cada vez que el gate de
    superficie corre, persiste estos dos campos bajo el gate correspondiente
    (`gates.<fase>.verificado_en`, `gates.<fase>.fs_hash`). Esta función
    los extrae del último gate con `veredicto ∈ {aprobado, refinado}`.
    """
    gates = meta.get("gates", {})
    if not isinstance(gates, dict):
        return {}
    for fase in ("codigo", "tasks", "plan", "spec"):
        g = gates.get(fase, {})
        if isinstance(g, dict):
            ver = g.get("verificado_en") or g.get("verificado_fecha")
            fs = g.get("fs_hash")
            if ver or fs:
                return {
                    "ultima_fase": fase,
                    "verificado_en": str(ver) if ver else "",
                    "fs_hash": str(fs) if fs else "",
                }
    return {}


def _emit_resume(unit_dir: Path) -> None:
    estado_path = unit_dir / "_estado.yaml"
    try:
        meta = _read_yaml_simple(estado_path)
    except Exception as exc:  # noqa: BLE001 — explicit exit code, not a crash
        print(f"ERROR — no se pudo leer {estado_path}: {exc}", file=sys.stderr)
        sys.exit(4)

    fase = str(meta.get("fase", "?"))
    modo = str(meta.get("modo", "interactivo"))
    riesgo = str(meta.get("riesgo", "medio"))
    perfil = str(meta.get("perfil", "estandar"))
    gates_block = meta.get("gates", "")
    aprobacion_block = meta.get("aprobacion_paquete", "")
    governance_block = meta.get("governance_refs", "")
    comando = str(meta.get("comando_validacion", ""))

    payload = {
        "kind": "resume",
        "unit": unit_dir.name,
        "fase": fase,
        "estado": str(meta.get("estado", "?")),
        "modo": modo,
        "riesgo": riesgo,
        "perfil": perfil,
        # Vigencia de gobernanza (unit 0003, fix 6, CA-12/CA-13): campos
        # opcionales que el script `check_governance_surface.py` rellena
        # cuando corre. Si están presentes, la sesión que los lee puede
        # detectar fs_hash desactualizado o verificado_en > 24h.
        "vigencia_gobernanza": _vigencia_gobernanza(meta),
        "next": PHASE_TO_SKILL.get(fase, "?"),
        "gates": gates_block or "{}",
        "aprobacion": aprobacion_block or "{}",
        "governance_refs": (
            [ln.strip().lstrip("- ").strip() for ln in governance_block.splitlines() if ln.strip().startswith("-")]
            if isinstance(governance_block, str) else []
        ),
        "tasks_pending": _tasks_pendientes(unit_dir / "tasks.md"),
        "last_event_line": _last_event_line(unit_dir / "bitacora.md"),
        "comando_validacion": comando,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    first_line = __doc__.splitlines()[0] if __doc__ else "sdd_retomar.py"
    ap = argparse.ArgumentParser(prog="sdd_retomar.py", description=first_line)
    ap.add_argument("hint", nargs="?", help="id, slug, or directory path")
    args = ap.parse_args()

    units = _listar_unidades()

    if args.hint is None:
        if not units:
            print("ERROR — no hay unidades bajo .spec/units/", file=sys.stderr)
            return 1
        if len(units) == 1:
            _emit_resume(UNITS_ROOT / units[0]["id"])
            return 0
        _emit_candidates(units)
        return 2

    unit_dir = _resolver(args.hint)
    if unit_dir is None:
        print(f"ERROR — ninguna unidad coincide con {args.hint!r}", file=sys.stderr)
        return 3
    _emit_resume(unit_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
