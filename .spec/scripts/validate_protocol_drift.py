#!/usr/bin/env python3
"""Drift detector for the SDD protocol — motor genérico del kit, ejecutable
desde bash / `pre-push-gate.sh`.

Cobertura (5 chequeos invariantes del protocolo SDD, válidos para cualquier
destino, verificables sin invocar el MCP):
  1. README ## Gates de validación coincide con el presupuesto golden por
     tier (bajo/medio/alto) — estructura del propio protocolo, no dato de
     ningún destino.
  2. Los agents declarados en `.claude/agents/` (los roles base más toda
     variante generada `<rol>-<effort>`, p. ej. `sdd-implementador-xhigh`; el
     manifiesto `.claude-agents-manifest.yaml` no matchea el glob y queda
     fuera) declaran un modelo de la familia real de Anthropic
     `{opus, sonnet, haiku}` -- invariante de cualquier destino que use
     agentes Claude, no dato propio de un destino (a diferencia de los
     checks 10/11, que sí leen su lista de modelos permitidos de
     configuración -- ver más abajo).
  3. Las `SKILL.md` de fase no contienen prosa que duplique el frontmatter
     de los agents: menciona explícitamente un modelo que la configuración
     de datos del destino no declara como permitido, o repite en prosa el
     par (modelo, effort) de un subagente que ya tiene su propio agent
     declarado.
  4. `.spec/perfiles.yaml` existe; sus perfiles top-level están en la lista
     de perfiles permitidos de la configuración de datos del destino, y
     ninguno de sus roles bajo `estandar` declara un modelo fuera de esa
     misma lista.
  5. Las skills que invocan subagentes citan `effort_profile.py resolve` en
     su `SKILL.md` canónico -- ninguna cae en silencio al frontmatter.

Los checks 3 y 4 (numerados `10` y `11` -- se conserva la numeración
original del validador de origen para trazabilidad, no se renumera a 01-05)
leen los nombres de modelo/perfil permitidos desde
`.spec/protocolo-datos.yaml` del destino (`--protocolo-datos` > variable de
entorno `SDD_PROTOCOLO_DATOS_PATH` > archivo de reposo en la raíz del
destino -- mismo patrón de precedencia que `preflight.py:resolve_mcp_config`
usa para el MCP), en vez de traerlos cableados: un destino sin ese archivo
hace fallar el motor de forma explícita, nunca con un default silencioso.

Los 8 checks que en el validador de origen (donde este archivo se generó)
cubrían historia puntual de ese repositorio -- referencias a documentos,
decisiones y artefactos que solo existen ahí -- no viajan como código de
este motor: cada destino los declara como sus propios datos si le aplican,
fuera de este archivo.

Salida:
  - Cada chequeo imprime `OK: <id> — <descripción>` o `FAIL: <id> — <descripción>`.
  - Exit 0 si los 5 chequeos pasan; exit 1 si alguno falla.

Convenciones:
  - stdlib only (`argparse`, `json`, `re`, `sys`, `pathlib`) más `_common`
    (sibling, mismo directorio).
  - Sin red, sin MCP. Drift local.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    ProtocoloDatosError,
    field_list,
    load_protocolo_datos,
    resolve_protocolo_datos_path,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
README = REPO_ROOT / ".spec" / "README.md"
PERFILES_YAML = REPO_ROOT / ".spec" / "perfiles.yaml"
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"

#: Familia real de modelos Anthropic -- invariante de cualquier destino que
#: use agentes Claude (check 02), a diferencia de los modelos/perfiles
#: *permitidos por este destino* que leen los checks 10/11 de
#: `.spec/protocolo-datos.yaml`.
ALLOWED_MODELS = {"opus", "sonnet", "haiku"}

#: Las skills que invocan subagentes (checks 10 y 12) -- cada destino puede
#: tener un conjunto distinto de skills de fase; el motor no asume una lista
#: cerrada propia de ningún destino salvo que la declare aquí como parte de
#: su propio `SKILL.md` (ver `check_12_skills_citan_effort_profile_resolve`,
#: que recorre `SKILLS_DIR` completo, no una lista cableada).
EXPECTED_GATES_TABLE = {
    "bajo": {"críticos": "1", "iteraciones": "1", "adversarial": "no"},
    "medio": {"críticos": "1", "iteraciones": "1", "adversarial": "no"},
    "alto": {"críticos": "2", "iteraciones": "2", "adversarial": "sí"},
}


def _load_protocolo_datos(flag: str | None) -> str:
    path, _origen = resolve_protocolo_datos_path(flag, REPO_ROOT)
    return load_protocolo_datos(path)


def _modelos_permitidos(datos_text: str) -> set[str]:
    items = field_list(datos_text, "modelos_permitidos")
    return {m.strip().lower() for m in (items or [])}


def _perfiles_permitidos(datos_text: str) -> set[str]:
    items = field_list(datos_text, "perfiles_permitidos")
    return {p.strip().lower() for p in (items or [])}


# --- Chequeos ----------------------------------------------------------------

def check_01_readme_gates_table() -> tuple[bool, str]:
    text = README.read_text(encoding="utf-8")
    m = re.search(r"^## Gates de validación\n(.*?)(?=^## |\Z)", text,
                  re.MULTILINE | re.DOTALL)
    if m is None:
        return False, "README no tiene sección `## Gates de validación`"
    section = m.group(1)
    rows: dict[str, dict[str, str]] = {}
    table_lines = [ln for ln in section.splitlines() if ln.strip().startswith("|")]
    for line in table_lines[2:]:
        inner = line.strip().strip("|")
        cells = [c.strip() for c in inner.split("|")]
        if len(cells) < 4:
            continue
        tier = cells[0].strip("`").lower()
        rows[tier] = {
            "críticos": cells[1],
            "iteraciones": cells[2],
            "adversarial": cells[3],
        }
    if set(rows.keys()) != set(EXPECTED_GATES_TABLE.keys()):
        return False, f"tiers {set(rows.keys())} != {set(EXPECTED_GATES_TABLE.keys())}"
    for tier, expected in EXPECTED_GATES_TABLE.items():
        if rows[tier] != expected:
            return False, f"tier {tier}: {rows[tier]} != {expected}"
    return True, f"3 tiers coinciden con golden ({EXPECTED_GATES_TABLE})"


def check_02_agents_models() -> tuple[bool, str]:
    """Los agents declarados bajo `.claude/agents/` -- los roles base más
    toda variante generada `<rol>-<effort>` (p. ej. `sdd-implementador-xhigh`,
    que también matchea el glob `sdd-*.md`) -- declaran modelo de la familia
    real de Anthropic {opus, sonnet, haiku}. El manifiesto
    `.claude-agents-manifest.yaml` no matchea `sdd-*.md` y queda fuera sin
    filtro extra.
    """
    if not AGENTS_DIR.is_dir():
        return False, f"no existe {AGENTS_DIR}"
    offenders: list[str] = []
    agent_files = sorted(AGENTS_DIR.glob("sdd-*.md"))
    for path in agent_files:
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, ln in enumerate(lines[:8]):  # frontmatter zone
            stripped = ln.strip()
            if stripped.startswith("model:"):
                model = stripped.split(":", 1)[1].strip()
                if model not in ALLOWED_MODELS:
                    offenders.append(f"{path.name}:{i+1} `{model}`")
    if offenders:
        return False, "; ".join(offenders)
    return True, f"{len(agent_files)} agents (roles + variantes) en lista permitida {sorted(ALLOWED_MODELS)}"


def check_10_skills_prosa_no_redundante(datos_text: str) -> tuple[bool, str]:
    r"""Las `SKILL.md` de `.agents/skills/` no contienen prosa redundante que
    duplique el frontmatter de los agents.

    Detecta dos clases de prosa redundante:
      (a) Mención explícita `(Modelo, effort ...)` donde `Modelo` (insensible
          a mayúsculas) no está en `modelos_permitidos` de
          `.spec/protocolo-datos.yaml` del destino -- generaliza la
          detección de nombres de modelo retirados/no autorizados sin
          cablear ningún nombre concreto en el código del motor.
      (b) Patrón `subagente `X` (Modelo, effort Y)` cuando existe
          `.claude/agents/X.md` con su propio frontmatter -- la prosa debe
          referenciar ese archivo en vez de duplicarlo, sea cual sea
          `Modelo` (este patrón no depende de configuración: un subagente
          con agent declarado siempre debe referenciarlo, no repetirlo).

    Solo verifica los canónicos `.agents/skills/` (los espejos `.claude/skills/`
    son read-only, regenerados con `materialize_claude_skills.py`).
    """
    if not SKILLS_DIR.is_dir():
        return False, f"no existe {SKILLS_DIR.relative_to(REPO_ROOT)}"

    modelos_permitidos = _modelos_permitidos(datos_text)
    model_effort_pattern = re.compile(r"\((\w+),\s*effort\s+(\w+)\)")
    subagente_pattern = re.compile(
        r"subagente\s+`([a-z][\w-]*)`\s*\(([^,]+?),\s*effort\s+([a-z]+)\)"
    )

    offenders: list[str] = []

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.is_file():
            continue
        text = skill_path.read_text(encoding="utf-8")

        def _line_of(offset: int) -> int:
            return text.count("\n", 0, offset) + 1

        # (a) modelo mencionado en prosa que no está en la lista permitida.
        for match in model_effort_pattern.finditer(text):
            modelo = match.group(1)
            if modelo.lower() not in modelos_permitidos:
                offenders.append(
                    f"{skill_path.relative_to(REPO_ROOT)}:{_line_of(match.start())} — "
                    f"modelo `{modelo}` fuera de `modelos_permitidos`"
                )
        # (b) subagente `X` (Modelo, effort Y) con agent declarado.
        for match in subagente_pattern.finditer(text):
            subagente = match.group(1)
            modelo = match.group(2).strip()
            effort = match.group(3).strip()
            agent_path = REPO_ROOT / ".claude" / "agents" / f"{subagente}.md"
            if agent_path.is_file():
                offenders.append(
                    rf"{skill_path.relative_to(REPO_ROOT)}:{_line_of(match.start())} — "
                    rf"`subagente `{subagente}` ({modelo}, effort {effort})` "
                    rf"duplica frontmatter de {agent_path.relative_to(REPO_ROOT)}"
                )

    if offenders:
        return False, "; ".join(offenders[:5]) + (
            f" (+{len(offenders) - 5} más)" if len(offenders) > 5 else ""
        )
    return True, f"SKILL.md de fase sin prosa redundante ({len(list(SKILLS_DIR.iterdir()))} dirs revisadas)"


def check_11_perfiles_yaml_dentro_de_lo_permitido(datos_text: str) -> tuple[bool, str]:
    """`.spec/perfiles.yaml` existe; sus perfiles top-level (`perfiles.<x>`)
    están en `perfiles_permitidos` de `.spec/protocolo-datos.yaml`, y el
    bloque `estandar:` no declara ningún `modelo:` fuera de
    `modelos_permitidos` -- generaliza "no declara fable" (literal, del
    validador de origen) a "no declara nada fuera de lo que el destino
    permite" (config-driven).
    """
    if not PERFILES_YAML.is_file():
        return False, f"no existe {PERFILES_YAML.relative_to(REPO_ROOT)}"
    text = PERFILES_YAML.read_text(encoding="utf-8")

    perfiles_permitidos = _perfiles_permitidos(datos_text)
    declared = set(re.findall(r"^  (\w[\w-]*):\n", text, re.MULTILINE))
    fuera_de_lo_permitido = sorted(declared - perfiles_permitidos)
    if fuera_de_lo_permitido:
        return False, f"perfiles declarados fuera de `perfiles_permitidos`: {fuera_de_lo_permitido}"

    m = re.search(r"^  estandar:\n(.*?)(?=^  \S|\Z)", text, re.MULTILINE | re.DOTALL)
    if m is None:
        return False, "no se encontró el bloque `perfiles.estandar` en perfiles.yaml"
    block = m.group(1)
    modelos_permitidos = _modelos_permitidos(datos_text)
    modelos_en_bloque = {mo.lower() for mo in re.findall(r"\bmodelo:\s*(\w+)", block)}
    fuera = sorted(modelos_en_bloque - modelos_permitidos)
    if fuera:
        return False, f"`perfiles.estandar` declara modelo(s) fuera de `modelos_permitidos`: {fuera}"
    return True, "perfiles y modelos de `perfiles.yaml` dentro de lo permitido por el destino"


def check_12_skills_citan_effort_profile_resolve() -> tuple[bool, str]:
    """Toda skill de `.agents/skills/` cuyo `SKILL.md` de hecho invoca un
    subagente -- contiene `subagent_type` o una llamada `Agent(` (la firma
    real de la invocación, no una mención en prosa: una skill puede
    *discutir* el concepto de subagente sin lanzar uno) -- cita
    `effort_profile.py resolve`, para que ninguna delegue en el frontmatter
    en silencio. No asume una lista cerrada de skills "de fase": recorre
    `SKILLS_DIR` completo y solo exige la cita a las que de hecho invocan.
    """
    if not SKILLS_DIR.is_dir():
        return False, f"no existe {SKILLS_DIR.relative_to(REPO_ROOT)}"
    offenders: list[str] = []
    checked = 0
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.is_file():
            continue
        text = skill_path.read_text(encoding="utf-8")
        invokes_subagent = "subagent_type" in text or "Agent(" in text
        if not invokes_subagent:
            continue
        checked += 1
        if "effort_profile.py resolve" not in text:
            offenders.append(f"{skill_dir.name}/SKILL.md sin `effort_profile.py resolve`")
    if offenders:
        return False, "; ".join(offenders)
    return True, f"{checked} SKILL.md que invocan subagentes citan `effort_profile.py resolve`"


CHECKS_DATA_DRIVEN = {"10", "11"}

CHECKS = [
    ("01", "README ## Gates de validación vs golden", check_01_readme_gates_table),
    ("02", "agents (roles + variantes) declaran modelo permitido", check_02_agents_models),
    ("10", "SKILL.md de fase sin prosa redundante de modelo/effort", check_10_skills_prosa_no_redundante),
    ("11", "perfiles.yaml dentro de lo permitido por el destino", check_11_perfiles_yaml_dentro_de_lo_permitido),
    ("12", "SKILL.md que invocan subagentes citan effort_profile.py resolve", check_12_skills_citan_effort_profile_resolve),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Drift detector del protocolo SDD (motor del kit)")
    parser.add_argument("--quiet", action="store_true", help="solo imprime fallos")
    parser.add_argument("--protocolo-datos", metavar="RUTA", default=None,
                         help="ruta explícita a protocolo-datos.yaml (> env "
                              "SDD_PROTOCOLO_DATOS_PATH > .spec/protocolo-datos.yaml de la raíz)")
    args = parser.parse_args()

    try:
        datos_text = _load_protocolo_datos(args.protocolo_datos)
    except ProtocoloDatosError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    failures = 0
    for cid, label, fn in CHECKS:
        try:
            if cid in CHECKS_DATA_DRIVEN:
                ok, detail = fn(datos_text)
            else:
                ok, detail = fn()
        except Exception as e:  # noqa: BLE001 — un fallo de parse es un fallo de drift
            ok, detail = False, f"excepción: {type(e).__name__}: {e}"
        marker = "OK" if ok else "FAIL"
        line = f"{marker}: {cid} — {label} — {detail}"
        if not args.quiet or not ok:
            print(line)
        if not ok:
            failures += 1

    if failures:
        print(f"\nFAILURES: {failures}/{len(CHECKS)} chequeos fallaron.",
              file=sys.stderr)
        return 1
    print(f"\nOK: {len(CHECKS)}/{len(CHECKS)} chequeos pasaron.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
