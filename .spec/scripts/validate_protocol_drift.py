#!/usr/bin/env python3
"""Drift detector for the SDD protocol — executable from bash / `pre-push-gate.sh`.

Cobertura (13 chequeos, todos verificables sin invocar el MCP):
  1. README ## Gates de validación coincide con golden (`test_gate_tier_budget_unchanged.py`).
  2. Los agents declarados en `.claude/agents/` (los 9 roles base más toda
     variante generada `<rol>-<effort>`, p. ej. `sdd-implementador-xhigh`; el
     manifiesto `.claude-agents-manifest.yaml` no matchea el glob y queda
     fuera) declaran modelo de la lista permitida `{opus, sonnet, haiku}` —
     sin Fable como default. Excepción: menciones en el cuerpo descriptivo
     están permitidas (no se persiguen en prosa).
  3. `MODELO-AGENTES.md` no contiene Fable en la columna "Modelo" de la tabla
     de asignación (mismas reglas).
  4. `MODELO-AGENTES.md` cita `0117-D1` en su bloque introductorio (es la decisión
     que retiró Fable; sin esa cita la tabla puede revertirse sin dejar
     rastro).
  5. `indicators.json` (K7/K9/K10) tiene metas `≈ 0` (post-0117-D1; los topes viejos
     `≤ 25%/≤ 5%/0` reintroducirían el patrón de gasto que motivó el kit).
  6. `sdd-gate/SKILL.md` cita `0117-D6` (panel solo en `spec`/`codigo`).
  7. `sdd-orquestar/SKILL.md` menciona fast-track (`0123-D1`).
  8. `sdd-orquestar/SKILL.md` menciona modo micro (`0126`).
  9. `.claude/settings.json` declara `model: sonnet` y `effortLevel: medium`
     (kit-desarrollo-sistecredito-D2, que revierte el `low` de 0117-D7).
  10. Las 6 `SKILL.md` de fase no contienen prosa redundante que duplique el
      frontmatter de los agents.
  11. `.spec/perfiles.yaml` existe y `perfiles.estandar` no declara `fable` en
      ningún rol ni en `implementador_complejo` -- Fable/Opus sólo son
      autorizables desde `profundo` (CA-15).
  12. Las 6 skills que invocan subagentes (`sdd-especificar`, `sdd-planificar`,
      `sdd-tareas`, `sdd-implementar`, `sdd-gate`, `sdd-orquestar`) citan
      `effort_profile.py resolve` en su `SKILL.md` canónico -- ninguna cae en
      silencio al frontmatter (H5, CA-16).
  13. `MODELO-AGENTES.md` cita `perfiles.yaml` como fuente de la tabla de
      modelo/effort por rol (CA-22).

Salida:
  - Cada chequeo imprime `OK: <id> — <descripción>` o `FAIL: <id> — <descripción>`.
  - Exit 0 si los 13 chequeos pasan; exit 1 con código si alguno falla.

Convenciones:
  - stdlib only (`argparse`, `json`, `re`, `sys`, `pathlib`).
  - Sin red, sin MCP. Drift local.
  - Reutiliza `_gates_table_rows` del test viejo vía import si está disponible;
    si no, reimplementa inline (sin duplicar lógica de cobertura).

CA-12 / CA-13 / CA-14 del spec 0158.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
MODELO_AGENTES = REPO_ROOT / ".spec" / "MODELO-AGENTES.md"
README = REPO_ROOT / ".spec" / "README.md"
INDICATORS = REPO_ROOT / ".spec" / "planes" / "kit-desarrollo-sistecredito" / "indicators.json"
GATE_SKILL = REPO_ROOT / ".agents" / "skills" / "sdd-gate" / "SKILL.md"
GATE_REFS = REPO_ROOT / ".agents" / "skills" / "sdd-gate" / "references"
ORQUESTAR_SKILL = REPO_ROOT / ".agents" / "skills" / "sdd-orquestar" / "SKILL.md"
SETTINGS_JSON = REPO_ROOT / ".claude" / "settings.json"
PERFILES_YAML = REPO_ROOT / ".spec" / "perfiles.yaml"
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"

ALLOWED_MODELS = {"opus", "sonnet", "haiku"}

#: The 6 phase skills that invoke subagents (CA-16) -- checks 12 and, via
#: `check_10_skills_prosa_no_redundante`, the redundant-prose check both
#: scope to `SKILLS_DIR`, but only these 6 must cite `effort_profile.py
#: resolve`.
PHASE_SKILLS = (
    "sdd-especificar",
    "sdd-planificar",
    "sdd-tareas",
    "sdd-implementar",
    "sdd-gate",
    "sdd-orquestar",
)
EXPECTED_GATES_TABLE = {
    "bajo": {"críticos": "1", "iteraciones": "1", "adversarial": "no"},
    "medio": {"críticos": "1", "iteraciones": "1", "adversarial": "no"},
    "alto": {"críticos": "2", "iteraciones": "2", "adversarial": "sí"},
}


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
    """Los agents declarados bajo `.claude/agents/` -- los 9 roles base más
    toda variante generada `<rol>-<effort>` (p. ej. `sdd-implementador-xhigh`,
    que también matchea el glob `sdd-*.md`) -- declaran modelo de la lista
    permitida {opus, sonnet, haiku}. El manifiesto
    `.claude-agents-manifest.yaml` no matchea `sdd-*.md` y queda fuera sin
    filtro extra.

    Excepción: si el cuerpo del archivo contiene la cadena literal
    `0117-D1` o `model: fable` dentro de un bloque que documenta el retiro
    (texto entre `# ` y el siguiente `# `, o dentro de `> bloquequote`),
    esa mención no cuenta como default.
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


def check_03_modelo_agentes_table() -> tuple[bool, str]:
    """MODELO-AGENTES.md no contiene Fable como Modelo en la tabla de
    asignación. Acepta menciones en prosa explicativa (líneas que NO son
    parte de una fila `| ... | ... |`).
    """
    text = MODELO_AGENTES.read_text(encoding="utf-8")
    table_lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")
                   and "|" in ln.strip()[1:]]
    offenders: list[str] = []
    for i, ln in enumerate(table_lines):
        cells = [c.strip() for c in ln.strip().strip("|").split("|") if c.strip()]
        # Comparación insensible a mayúsculas, como ya hace el chequeo 11 sobre
        # `perfiles.yaml`: la versión anterior comparaba contra el literal
        # `"Fable"` y dejaba pasar una celda escrita `fable` o `FABLE`.
        hits = [c for c in cells if c.casefold() == "fable"]
        if hits:
            offenders.append(f"línea {i+1}: {hits}")
    if offenders:
        return False, "; ".join(offenders)
    return True, "tabla de asignación sin Fable como Modelo"


def check_04_modelo_agentes_cita_0117_d1() -> tuple[bool, str]:
    text = MODELO_AGENTES.read_text(encoding="utf-8")
    if "0117-D1" not in text:
        return False, "no se encontró la cadena `0117-D1`"
    return True, "cita `0117-D1` (decisión de retiro de Fable)"


def check_05_indicators_k7_k9_k10() -> tuple[bool, str]:
    """`indicators.json` K7/K9/K10 tienen metas `absolute = 0` (post-0117-D1;
    no `≤25%`/`≤5%` que reintroducirían el patrón de gasto pre-kit)."""
    if not INDICATORS.is_file():
        return False, f"no existe {INDICATORS.relative_to(REPO_ROOT)}"
    data = json.loads(INDICATORS.read_text(encoding="utf-8"))
    indicators = {i["id"]: i for i in data.get("indicators", [])}
    expected_targets = {"K7": 0.0, "K9": 0.0, "K10": 0}
    offenders: list[str] = []
    for kid, expected in expected_targets.items():
        if kid not in indicators:
            offenders.append(f"{kid} no existe")
            continue
        actual = indicators[kid].get("target", {}).get("absolute")
        if actual != expected:
            offenders.append(f"{kid}: target.absolute={actual} != {expected}")
    if offenders:
        return False, "; ".join(offenders)
    return True, "K7/K9/K10 con metas ≈0 (post-0117-D1)"


def check_06_gate_skill_cita_0117_d6() -> tuple[bool, str]:
    """`sdd-gate/SKILL.md` cita `0117-D6` (panel solo en `spec`/`codigo`).

    El chequeo busca en SKILL.md Y en `references/` — `0117-D6` puede vivir en
    `deterministic-layer.md` (donde el contrato está implementado).
    """
    text = GATE_SKILL.read_text(encoding="utf-8")
    if "0117-D6" in text:
        return True, "cita `0117-D6` en SKILL.md"
    if GATE_REFS.is_dir():
        for ref in sorted(GATE_REFS.glob("*.md")):
            if "0117-D6" in ref.read_text(encoding="utf-8"):
                return True, f"cita `0117-D6` en {ref.relative_to(REPO_ROOT)}"
    return False, "no se encontró `0117-D6` en SKILL.md ni en references/"


def check_07_orquestar_fast_track() -> tuple[bool, str]:
    text = ORQUESTAR_SKILL.read_text(encoding="utf-8")
    if "0123-D1" not in text and "fast-track" not in text.lower():
        return False, "no menciona fast-track ni `0123-D1`"
    return True, "menciona fast-track (0123-D1)"


def check_08_orquestar_modo_micro() -> tuple[bool, str]:
    text = ORQUESTAR_SKILL.read_text(encoding="utf-8")
    if "0126" not in text and "modo micro" not in text.lower():
        return False, "no menciona modo micro ni `0126`"
    return True, "menciona modo micro (0126)"


def check_09_settings_json_sesion() -> tuple[bool, str]:
    if not SETTINGS_JSON.is_file():
        return False, f"no existe {SETTINGS_JSON.relative_to(REPO_ROOT)}"
    data = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
    if data.get("model") != "sonnet":
        return False, f"settings.model={data.get('model')} != 'sonnet'"
    if data.get("effortLevel") != "medium":
        return False, f"settings.effortLevel={data.get('effortLevel')} != 'medium'"
    return True, "settings.json model=sonnet, effortLevel=medium (kit-desarrollo-sistecredito-D2)"


def check_10_skills_prosa_no_redundante() -> tuple[bool, str]:
    r"""Las 6 SKILL.md de fase no contienen prosa redundante que duplique el
    frontmatter de los agents.

    Detecta dos clases de prosa redundante:
      (a) Menciones literales de modelos retirados o esfuerzos pre-0117-D1/D2/D3:
          `(Fable, effort`, `(Sonnet, effort alto)`,
          `(Sonnet, effort medio)`, `(Sonnet, effort bajo)`.
      (b) Patrón `subagente `X` (Modelo, effort Y)` cuando existe
          `.claude/agents/X.md` con su propio frontmatter — la prosa debe
          decir "ver `.claude/agents/<subagente>.md`" en vez de duplicar.

    Solo verifica los canónicos `.agents/skills/` (los espejos `.claude/skills/`
    son read-only regenerados con `materialize_claude_skills.py`).
    """
    skills_dir = REPO_ROOT / ".agents" / "skills"
    if not skills_dir.is_dir():
        return False, f"no existe {skills_dir.relative_to(REPO_ROOT)}"

    # (a) Patrones regex de prosa redundante pre-0117-D1/D2/D3.
    # Incluyen la palabra `effort` cerca para no matchear menciones históricas
    # sueltas (ej. rationale que explica por qué se retiró).
    patterns = [
        (r"\(Fable,\s*effort\b", "Fable + effort (pre-0117-D1)"),
        (r"\(Sonnet,\s*effort\s+alto\)", "Sonnet effort alto (pre-0117-D3)"),
        (r"\(Sonnet,\s*effort\s+medio\)", "Sonnet effort medio (pre-0117-D3)"),
        (r"\(Sonnet,\s*effort\s+bajo\)", "Sonnet effort bajo (pre-0117-D3)"),
    ]

    offenders: list[str] = []

    # Patrón (b): subagente `X` (Modelo, effort Y) — si X tiene agent declarado,
    # la prosa es redundante y debe ser reemplazada por una referencia.
    # El patrón usa comillas tipográficas invertidas (backticks en markdown).
    subagente_pattern = re.compile(
        r"subagente\s+`([a-z][\w-]*)`\s*\(([^,]+?),\s*effort\s+([a-z]+)\)"
    )

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.is_file():
            continue
        # El barrido va sobre el texto COMPLETO, no línea a línea: los patrones
        # usan `\s*`/`\s+`, que ya casan un salto de línea, así que el mismo
        # patrón prohibido partido en dos renglones por el ancho de línea
        # escapaba de la versión anterior. La línea se deriva del offset para
        # que el mensaje de error siga señalando dónde está.
        text = skill_path.read_text(encoding="utf-8")

        def _line_of(offset: int) -> int:
            return text.count("\n", 0, offset) + 1

        for regex, label in patterns:
            for match in re.finditer(regex, text):
                offenders.append(
                    f"{skill_path.relative_to(REPO_ROOT)}:{_line_of(match.start())} — `{label}`"
                )
        # (b) subagente `X` (Modelo, effort Y) con agent declarado
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
    return True, f"6 SKILL.md de fase sin prosa redundante ({len(list(skills_dir.iterdir()))} dirs revisadas)"


def check_11_perfiles_yaml_estandar_sin_fable() -> tuple[bool, str]:
    """`.spec/perfiles.yaml` existe y el bloque `perfiles.estandar` no
    declara `fable` en ningún rol ni en `implementador_complejo` (CA-15) --
    la autorización de Fable/Opus (`0117-D1`) es exclusiva de `profundo`.

    No valida el esquema completo de `perfiles.yaml` (eso es
    `effort_profile.py load_profiles`); solo aísla el texto del bloque
    `estandar:` (2 espacios de indentación, hasta el próximo perfil al mismo
    nivel) y busca la palabra `fable` en él -- mismo estilo de escaneo de
    texto que el chequeo 03 sobre `MODELO-AGENTES.md`.
    """
    if not PERFILES_YAML.is_file():
        return False, f"no existe {PERFILES_YAML.relative_to(REPO_ROOT)}"
    text = PERFILES_YAML.read_text(encoding="utf-8")
    m = re.search(r"^  estandar:\n(.*?)(?=^  \S|\Z)", text, re.MULTILINE | re.DOTALL)
    if m is None:
        return False, "no se encontró el bloque `perfiles.estandar` en perfiles.yaml"
    block = m.group(1)
    if re.search(r"\bfable\b", block, re.IGNORECASE):
        return False, "`perfiles.estandar` declara `fable`"
    return True, "`perfiles.estandar` no declara `fable` en ningún rol"


def check_12_skills_citan_effort_profile_resolve() -> tuple[bool, str]:
    """Las 6 skills que invocan subagentes citan `effort_profile.py resolve`
    en su `SKILL.md` canónico (CA-16) -- evita que una skill vuelva a delegar
    en el frontmatter en silencio (H5)."""
    if not SKILLS_DIR.is_dir():
        return False, f"no existe {SKILLS_DIR.relative_to(REPO_ROOT)}"
    offenders: list[str] = []
    for name in PHASE_SKILLS:
        path = SKILLS_DIR / name / "SKILL.md"
        if not path.is_file():
            offenders.append(f"{name}/SKILL.md no existe")
            continue
        if "effort_profile.py resolve" not in path.read_text(encoding="utf-8"):
            offenders.append(f"{name}/SKILL.md sin `effort_profile.py resolve`")
    if offenders:
        return False, "; ".join(offenders)
    return True, "6 SKILL.md de fase citan `effort_profile.py resolve`"


def check_13_modelo_agentes_cita_perfiles_yaml() -> tuple[bool, str]:
    """`MODELO-AGENTES.md` cita `perfiles.yaml` como fuente de la tabla de
    modelo/effort por rol (CA-22)."""
    text = MODELO_AGENTES.read_text(encoding="utf-8")
    if "perfiles.yaml" not in text:
        return False, "no se encontró la cadena `perfiles.yaml`"
    return True, "cita `perfiles.yaml` como fuente de la tabla"


CHECKS = [
    ("01", "README ## Gates de validación vs golden", check_01_readme_gates_table),
    ("02", "agents (roles + variantes) declaran modelo permitido", check_02_agents_models),
    ("03", "MODELO-AGENTES.md tabla sin Fable", check_03_modelo_agentes_table),
    ("04", "MODELO-AGENTES.md cita 0117-D1", check_04_modelo_agentes_cita_0117_d1),
    ("05", "indicators.json K7/K9/K10 ≈ 0", check_05_indicators_k7_k9_k10),
    ("06", "sdd-gate/SKILL.md cita 0117-D6", check_06_gate_skill_cita_0117_d6),
    ("07", "sdd-orquestar/SKILL.md fast-track", check_07_orquestar_fast_track),
    ("08", "sdd-orquestar/SKILL.md modo micro", check_08_orquestar_modo_micro),
    ("09", "settings.json model=sonnet, effortLevel=medium", check_09_settings_json_sesion),
    ("10", "6 SKILL.md de fase sin prosa redundante de modelo/effort", check_10_skills_prosa_no_redundante),
    ("11", "perfiles.estandar sin Fable", check_11_perfiles_yaml_estandar_sin_fable),
    ("12", "6 SKILL.md de fase citan effort_profile.py resolve", check_12_skills_citan_effort_profile_resolve),
    ("13", "MODELO-AGENTES.md cita perfiles.yaml", check_13_modelo_agentes_cita_perfiles_yaml),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Drift detector del protocolo SDD")
    parser.add_argument("--quiet", action="store_true", help="solo imprime fallos")
    args = parser.parse_args()

    failures = 0
    for cid, label, fn in CHECKS:
        try:
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
