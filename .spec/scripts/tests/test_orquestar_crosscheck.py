"""Tests del cross-check de superficie del orquestador (unit 0003, fix 8).

Cubre CA-17 (sección existe), CA-18a (test estructural), CA-18b (test
funcional que reproduce el incidente del 2026-09-29).

Convención de nombres: `u0003_ca<NN>_*` para que el gate de tasks los
pueda coleccionar.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".spec" / "scripts" / "check_governance_surface.py"
ORQUESTAR_SKILL = REPO_ROOT / ".agents" / "skills" / "sdd-orquestar" / "SKILL.md"


# --- u0003_ca18a: test estructural ---

def test_u0003_ca18a_section_exists_with_three_actions() -> None:
    text = ORQUESTAR_SKILL.read_text(encoding="utf-8")
    assert "## Cross-check de superficie" in text, (
        "la sección 'Cross-check de superficie' debe existir en sdd-orquestar/SKILL.md"
    )
    # Las tres acciones deben aparecer como items enumerados (1, 2, 3) o
    # frases distinguibles. Buscamos las palabras clave.
    assert re.search(r"1\.\s+\*\*[^*]*existencia", text) or "existencia" in text
    assert re.search(r"2\.\s+\*\*[^*]*grep", text) or ("grep" in text and "find" in text)
    assert re.search(r"3\.\s+\*\*[^*]*Comparar", text) or "Comparar" in text or "comparar" in text


def test_u0003_ca18a_section_references_incident() -> None:
    text = ORQUESTAR_SKILL.read_text(encoding="utf-8")
    assert "2026-09-29" in text, (
        "la sección debe referenciar el incidente del 2026-09-29 para anclar la regla"
    )


# --- u0003_ca17: la sección existe (subsume por test_u0003_ca18a) ---

def test_u0003_ca17_section_present() -> None:
    text = ORQUESTAR_SKILL.read_text(encoding="utf-8")
    assert "## Cross-check de superficie" in text
    assert "fix 8" in text or "Fix 8" in text
    assert "CA-17" in text


# --- u0003_ca18b: test funcional que reproduce el incidente del 2026-09-29 ---

def test_u0003_ca18b_incident_reproduced(tmp_path: Path) -> None:
    """El incidente del 2026-09-29 se reproduce con un filesystem simulado
    que contiene `opencode.jsonc` declarando `pce-mcp` y un explorador
    simulado que devuelve "no surface". El veredicto correcto del gate es
    `escalado` (no `aprobado`): el cross-check del orquestador detecta la
    divergencia y escala.

    Esta prueba verifica el comportamiento del script nuevo
    (T8 de la unit 0003: el paso 3 del gate invoca el script y traduce
    el Report a veredicto). Si el script pasa con `ok` cuando debería
    detectar `superficie-no-declarada`, la prueba falla.
    """
    spec = tmp_path / "spec.md"
    spec.write_text(
        "# Test spec\n\n## Configuration contract\n\n"
        "```\nconfiguration_contract: [\".mcp.json\"]\n```\n",
        encoding="utf-8",
    )
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))
    (tmp_path / "opencode.jsonc").write_text(
        json.dumps({"mcp": {"pce-mcp": {"type": "local"}}})
    )

    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--unit", str(tmp_path),
         "--spec", str(spec),
         "--filesystem-root", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 1, (
        f"el script debe detectar superficie-no-declarada (exit 1), "
        f"no aprobar (exit 0). El incidente del 2026-09-29 ocurrió porque "
        f"el gate aprobó con evidencia insuficiente. stderr={r.stderr!r}"
    )
    payload = json.loads(r.stdout)
    assert payload["verdict"] == "superficie-no-declarada"
    assert "opencode.jsonc" in payload["found"]
    assert ".mcp.json" in payload["found"]


# --- u0003_ca08 (estructura del fallback) ---

def test_u0003_ca08_subagent_fallback_exists() -> None:
    path = REPO_ROOT / ".agents" / "skills" / "sdd-gate" / "references" / "subagent-fallback.md"
    assert path.is_file(), f"falta {path}"
    text = path.read_text(encoding="utf-8")
    assert "Política" in text
    assert "Roles cubiertos" in text
    for rol in ("sdd-explorador", "sdd-planificar-redactor", "sdd-tareas-redactor",
                "sdd-critico-profundo", "sdd-critico-estructural", "sdd-critico-cumplimiento"):
        assert rol in text, f"el rol {rol} debe estar listado"


def test_u0003_ca08_skill_references_fallback() -> None:
    sdd_gate = (REPO_ROOT / ".agents" / "skills" / "sdd-gate" / "SKILL.md").read_text(encoding="utf-8")
    assert "references/subagent-fallback.md" in sdd_gate
    assert "generaliza" in sdd_gate or "generaliza" in sdd_gate.lower()
