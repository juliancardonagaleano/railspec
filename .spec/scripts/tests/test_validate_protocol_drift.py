"""Tests for `validate_protocol_drift.py` — drift detector del protocolo SDD.

Cobertura (CA-13 del spec 0158):
  - Caso feliz: protocolo consistente → exit 0 con 9/9 chequeos OK.
  - Drift simulado por chequeo (8 casos): cada `check_NN_*` retorna FAIL cuando
    se simula la condición de drift (cambio puntual al archivo vigilado).
  - El chequeo 09 (settings.json) acepta `effortLevel` ausente sin fallar
    (compatibilidad hacia atrás con clones que aún no tengan 0117-D7).
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import validate_protocol_drift as vpd

REPO_ROOT = vpd.REPO_ROOT


class _TmpProtocol:
    """Crea un árbol mínimo del protocolo SDD en un directorio temporal y
    ejecuta los chequeos contra él. Cada test invoca `_set(...)` con el
    contenido exacto de cada archivo vigilado."""

    #: The 6 phase skills `check_12_...` requires (mirrors `vpd.PHASE_SKILLS`).
    PHASE_SKILLS = (
        "sdd-especificar",
        "sdd-planificar",
        "sdd-tareas",
        "sdd-implementar",
        "sdd-gate",
        "sdd-orquestar",
    )

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        (tmp / ".claude" / "agents").mkdir(parents=True)
        (tmp / ".agents" / "skills" / "sdd-gate" / "references").mkdir(parents=True)
        (tmp / ".agents" / "skills" / "sdd-orquestar").mkdir(parents=True)
        (tmp / ".spec").mkdir(parents=True)
        (tmp / ".spec" / "planes" / "kit-desarrollo-sistecredito").mkdir(parents=True)

    def _write(self, rel: str, content: str) -> None:
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def set_consistent(self) -> None:
        # 9 agents en lista permitida
        for name, model, effort in [
            ("critico-cumplimiento", "sonnet", "high"),
            ("critico-estructural", "haiku", "medium"),
            ("critico-profundo", "sonnet", "high"),
            ("especificar-redactor", "sonnet", "high"),
            ("explorador", "sonnet", "low"),
            ("implementador", "sonnet", "medium"),
            ("planificar-redactor", "sonnet", "high"),
            ("refutador", "sonnet", "medium"),
            ("tareas-redactor", "haiku", "medium"),
        ]:
            self._write(f".claude/agents/sdd-{name}.md",
                        f"---\nmodel: {model}\neffort: {effort}\n---\n")
        # MODELO-AGENTES sin Fable/Opus en tabla + cita 0117-D1 + perfiles.yaml
        self._write(".spec/MODELO-AGENTES.md",
                    "## Asignación acordada (Julian, 2026-09-20, 0117-D1)\n\n"
                    "Fuente: `.spec/perfiles.yaml > estandar`.\n\n"
                    "| Rol | Modelo | Effort |\n|---|---|---|\n| X | Sonnet | low |\n")
        # README con tabla golden
        self._write(".spec/README.md",
                    "## Gates de validación\n\n"
                    "| Tier | Críticos | Iter | Adv |\n|---|---|---|---|\n"
                    "| bajo | 1 | 1 | no |\n| medio | 1 | 1 | no |\n| alto | 2 | 2 | sí |\n\n## Otros\n")
        # indicators.json con K7/K9/K10 = 0
        self._write(".spec/planes/kit-desarrollo-sistecredito/indicators.json",
                    json.dumps({"indicators": [
                        {"id": "K7", "target": {"absolute": 0.0}},
                        {"id": "K9", "target": {"absolute": 0.0}},
                        {"id": "K10", "target": {"absolute": 0}},
                    ]}))
        # sdd-gate SKILL + references con 0117-D6
        self._write(".agents/skills/sdd-gate/SKILL.md", "")
        self._write(".agents/skills/sdd-gate/references/deterministic-layer.md",
                    "panel solo en `spec`/`codigo` (`0117-D6`).\n")
        # sdd-orquestar con fast-track + modo micro + cita al resolvedor
        self._write(".agents/skills/sdd-orquestar/SKILL.md",
                    "fast-track (`0123-D1`)\nmicro (`0126`)\n"
                    "`effort_profile.py resolve --explorers --tier <tier>`\n")
        # settings.json con model=sonnet, effortLevel=medium — el valor que el
        # chequeo 09 exige, y el que lleva el repo real.
        self._write(".claude/settings.json",
                    json.dumps({"model": "sonnet", "effortLevel": "medium"}))
        # perfiles.yaml: `estandar` sin fable (CA-15)
        self._write(".spec/perfiles.yaml",
                    "default: estandar\n\nperfiles:\n  estandar:\n"
                    "    roles:\n      sdd-critico-profundo: {modelo: sonnet, effort: high}\n"
                    "    implementador_complejo: {modelo: sonnet, effort: xhigh}\n\n"
                    "  profundo:\n"
                    "    roles:\n      sdd-critico-profundo: {modelo: fable}\n")
        # las 6 skills de fase citan el resolvedor (CA-16); sdd-gate y
        # sdd-orquestar ya se escribieron arriba con su propio contenido.
        for name in self.PHASE_SKILLS:
            if name in ("sdd-gate", "sdd-orquestar"):
                continue
            self._write(f".agents/skills/{name}/SKILL.md",
                        "`effort_profile.py resolve --role <rol>`\n")
        self._write(".agents/skills/sdd-gate/SKILL.md",
                    "panel solo en `spec`/`codigo` (`0117-D6`).\n"
                    "`effort_profile.py resolve --gate --tier <tier>`\n")


class HappyPathTests(unittest.TestCase):
    """Caso feliz: protocolo consistente → 9/9 OK, exit 0."""

    def setUp(self) -> None:
        import tempfile
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.proto = _TmpProtocol(self.tmp)
        self.proto.set_consistent()
        # monkey-patch REPO_ROOT y paths derivados
        self._patches = []
        for attr in ("REPO_ROOT", "AGENTS_DIR", "MODELO_AGENTES", "README",
                     "INDICATORS", "GATE_SKILL", "GATE_REFS", "ORQUESTAR_SKILL",
                     "SETTINGS_JSON", "PERFILES_YAML", "SKILLS_DIR"):
            original = getattr(vpd, attr)
            self._patches.append((vpd, attr, original))
        vpd.REPO_ROOT = self.tmp
        vpd.AGENTS_DIR = self.tmp / ".claude" / "agents"
        vpd.MODELO_AGENTES = self.tmp / ".spec" / "MODELO-AGENTES.md"
        vpd.README = self.tmp / ".spec" / "README.md"
        vpd.INDICATORS = self.tmp / ".spec" / "planes" / "kit-desarrollo-sistecredito" / "indicators.json"
        vpd.GATE_SKILL = self.tmp / ".agents" / "skills" / "sdd-gate" / "SKILL.md"
        vpd.GATE_REFS = self.tmp / ".agents" / "skills" / "sdd-gate" / "references"
        vpd.ORQUESTAR_SKILL = self.tmp / ".agents" / "skills" / "sdd-orquestar" / "SKILL.md"
        vpd.SETTINGS_JSON = self.tmp / ".claude" / "settings.json"
        vpd.PERFILES_YAML = self.tmp / ".spec" / "perfiles.yaml"
        vpd.SKILLS_DIR = self.tmp / ".agents" / "skills"

    def tearDown(self) -> None:
        for obj, attr, original in self._patches:
            setattr(obj, attr, original)
        self.tmp_ctx.cleanup()

    def test_main_returns_0_when_protocol_consistent(self) -> None:
        # `main()` parses the process arguments, which under a test runner are
        # the runner's own. Give it a bare argv so it parses its defaults.
        buf = io.StringIO()
        original_argv = sys.argv
        sys.argv = [original_argv[0]]
        try:
            with redirect_stdout(buf):
                rc = vpd.main()
        finally:
            sys.argv = original_argv
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("13/13 chequeos pasaron", output)
        for cid in ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10",
                    "11", "12", "13"):
            self.assertIn(f"OK: {cid}", output)


class DriftSimulationTests(unittest.TestCase):
    """Drift simulado: 8 chequeos (todos salvo 09) deben retornar FAIL."""

    def setUp(self) -> None:
        import tempfile
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.proto = _TmpProtocol(self.tmp)
        self.proto.set_consistent()
        self._patches = []
        for attr in ("REPO_ROOT", "AGENTS_DIR", "MODELO_AGENTES", "README",
                     "INDICATORS", "GATE_SKILL", "GATE_REFS", "ORQUESTAR_SKILL",
                     "SETTINGS_JSON", "PERFILES_YAML", "SKILLS_DIR"):
            original = getattr(vpd, attr)
            self._patches.append((vpd, attr, original))
        vpd.REPO_ROOT = self.tmp
        vpd.AGENTS_DIR = self.tmp / ".claude" / "agents"
        vpd.MODELO_AGENTES = self.tmp / ".spec" / "MODELO-AGENTES.md"
        vpd.README = self.tmp / ".spec" / "README.md"
        vpd.INDICATORS = self.tmp / ".spec" / "planes" / "kit-desarrollo-sistecredito" / "indicators.json"
        vpd.GATE_SKILL = self.tmp / ".agents" / "skills" / "sdd-gate" / "SKILL.md"
        vpd.GATE_REFS = self.tmp / ".agents" / "skills" / "sdd-gate" / "references"
        vpd.ORQUESTAR_SKILL = self.tmp / ".agents" / "skills" / "sdd-orquestar" / "SKILL.md"
        vpd.SETTINGS_JSON = self.tmp / ".claude" / "settings.json"
        vpd.PERFILES_YAML = self.tmp / ".spec" / "perfiles.yaml"
        vpd.SKILLS_DIR = self.tmp / ".agents" / "skills"

    def tearDown(self) -> None:
        for obj, attr, original in self._patches:
            setattr(obj, attr, original)
        self.tmp_ctx.cleanup()

    def test_01_readme_gates_table_drift(self) -> None:
        # Cambiar la tabla del README a valores viejos
        vpd.README.write_text(
            "## Gates de validación\n\n"
            "| Tier | Críticos | Iter | Adv |\n|---|---|---|---|\n"
            "| bajo | 1 | 1 | no |\n| medio | 2 | 2 | no |\n| alto | 3 | 3 | sí |\n\n",
            encoding="utf-8")
        ok, _ = vpd.check_01_readme_gates_table()
        self.assertFalse(ok)

    def test_02_agents_models_drift(self) -> None:
        # Cambiar un agent a fable
        (self.tmp / ".claude/agents" / "sdd-especificar-redactor.md").write_text(
            "---\nmodel: fable\neffort: high\n---\n", encoding="utf-8")
        ok, detail = vpd.check_02_agents_models()
        self.assertFalse(ok)
        self.assertIn("fable", detail)

    def test_03_modelo_agentes_table_drift(self) -> None:
        vpd.MODELO_AGENTES.write_text(
            "## Asignación acordada (Julian, 2026-09-20)\n\n"
            "| Rol | Modelo | Effort |\n|---|---|---|\n| X | Fable | high |\n",
            encoding="utf-8")
        ok, _ = vpd.check_03_modelo_agentes_table()
        self.assertFalse(ok)

    def test_04_modelo_agentes_sin_0117_d1(self) -> None:
        vpd.MODELO_AGENTES.write_text(
            "## Asignación acordada (Julian, 2026-09-17)\n\n"
            "| Rol | Modelo | Effort |\n|---|---|---|\n| X | Sonnet | low |\n",
            encoding="utf-8")
        ok, _ = vpd.check_04_modelo_agentes_cita_0117_d1()
        self.assertFalse(ok)

    def test_05_indicators_k7_drift(self) -> None:
        vpd.INDICATORS.write_text(json.dumps({"indicators": [
            {"id": "K7", "target": {"absolute": 0.25}},
            {"id": "K9", "target": {"absolute": 0.0}},
            {"id": "K10", "target": {"absolute": 0}},
        ]}), encoding="utf-8")
        ok, _ = vpd.check_05_indicators_k7_k9_k10()
        self.assertFalse(ok)

    def test_06_gate_skill_sin_0117_d6(self) -> None:
        # Vaciar SKILL y references
        vpd.GATE_SKILL.write_text("", encoding="utf-8")
        for ref in vpd.GATE_REFS.glob("*.md"):
            ref.write_text("", encoding="utf-8")
        ok, _ = vpd.check_06_gate_skill_cita_0117_d6()
        self.assertFalse(ok)

    def test_07_orquestar_sin_fast_track(self) -> None:
        vpd.ORQUESTAR_SKILL.write_text(
            "modo micro (`0126`)\n", encoding="utf-8")
        ok, _ = vpd.check_07_orquestar_fast_track()
        self.assertFalse(ok)

    def test_08_orquestar_sin_modo_micro(self) -> None:
        vpd.ORQUESTAR_SKILL.write_text(
            "fast-track (`0123-D1`)\n", encoding="utf-8")
        ok, _ = vpd.check_08_orquestar_modo_micro()
        self.assertFalse(ok)

    def test_10_prosa_fable_en_especificar(self) -> None:
        """Drift del chequeo 10: reintroducir `(Fable, effort alto)` en una skill
        de fase debe disparar FAIL."""
        (self.tmp / ".agents" / "skills" / "sdd-especificar").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".agents" / "skills" / "sdd-especificar" / "SKILL.md").write_text(
            "Invocar al subagente `sdd-especificar-redactor` (Fable, effort alto).\n",
            encoding="utf-8")
        ok, detail = vpd.check_10_skills_prosa_no_redundante()
        self.assertFalse(ok)
        self.assertIn("Fable", detail)

    def test_10_prosa_sonnet_alto_en_tareas(self) -> None:
        """Drift del chequeo 10: reintroducir `(Sonnet, effort alto)` en una skill
        de fase debe disparar FAIL."""
        (self.tmp / ".agents" / "skills" / "sdd-tareas").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".agents" / "skills" / "sdd-tareas" / "SKILL.md").write_text(
            "Invocar al subagente `sdd-tareas-redactor` (Sonnet, effort alto).\n",
            encoding="utf-8")
        ok, detail = vpd.check_10_skills_prosa_no_redundante()
        self.assertFalse(ok)
        self.assertIn("Sonnet effort alto", detail)

    def test_10_patron_subagente_con_agent_declarado(self) -> None:
        """Drift del chequeo 10: patrón `subagente `X` (Modelo, effort Y)` cuando
        existe `.claude/agents/X.md` con su propio frontmatter — la prosa debe
        referenciar al agent, no duplicarlo."""
        (self.tmp / ".agents" / "skills" / "sdd-orquestar").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".agents" / "skills" / "sdd-orquestar" / "SKILL.md").write_text(
            "delegar al subagente `sdd-especificar-redactor` (sonnet, effort high)\n",
            encoding="utf-8")
        ok, detail = vpd.check_10_skills_prosa_no_redundante()
        self.assertFalse(ok)
        self.assertIn("sdd-especificar-redactor", detail)
        self.assertIn("duplica frontmatter", detail)

    def test_11_perfiles_yaml_missing(self) -> None:
        vpd.PERFILES_YAML.unlink()
        ok, detail = vpd.check_11_perfiles_yaml_estandar_sin_fable()
        self.assertFalse(ok)
        self.assertIn("no existe", detail)

    def test_11_perfiles_yaml_estandar_declares_fable(self) -> None:
        vpd.PERFILES_YAML.write_text(
            "default: estandar\n\nperfiles:\n  estandar:\n"
            "    roles:\n      sdd-especificar-redactor: {modelo: fable, effort: high}\n"
            "    implementador_complejo: {modelo: sonnet, effort: xhigh}\n",
            encoding="utf-8")
        ok, detail = vpd.check_11_perfiles_yaml_estandar_sin_fable()
        self.assertFalse(ok)
        self.assertIn("fable", detail)

    def test_11_perfiles_yaml_profundo_declaring_fable_does_not_fail(self) -> None:
        """`profundo` (o cualquier perfil que no sea `estandar`) puede
        declarar Fable/Opus sin disparar el chequeo 11."""
        ok, _ = vpd.check_11_perfiles_yaml_estandar_sin_fable()
        self.assertTrue(ok)

    def test_12_a_phase_skill_stops_citing_the_resolver(self) -> None:
        (self.tmp / ".agents" / "skills" / "sdd-tareas" / "SKILL.md").write_text(
            "descompone el plan en tareas\n", encoding="utf-8")
        ok, detail = vpd.check_12_skills_citan_effort_profile_resolve()
        self.assertFalse(ok)
        self.assertIn("sdd-tareas/SKILL.md", detail)

    def test_12_a_phase_skill_is_missing_entirely(self) -> None:
        import shutil
        shutil.rmtree(self.tmp / ".agents" / "skills" / "sdd-implementar")
        ok, detail = vpd.check_12_skills_citan_effort_profile_resolve()
        self.assertFalse(ok)
        self.assertIn("sdd-implementar/SKILL.md no existe", detail)

    def test_13_modelo_agentes_stops_citing_perfiles_yaml(self) -> None:
        vpd.MODELO_AGENTES.write_text(
            "## Asignación acordada (Julian, 2026-09-20, 0117-D1)\n\n"
            "| Rol | Modelo | Effort |\n|---|---|---|\n| X | Sonnet | low |\n",
            encoding="utf-8")
        ok, detail = vpd.check_13_modelo_agentes_cita_perfiles_yaml()
        self.assertFalse(ok)
        self.assertIn("no se encontró", detail)


class ExecutableFromBashTests(unittest.TestCase):
    """El script debe ser ejecutable como `python3 validate_protocol_drift.py`
    y respetar exit codes (CA-12)."""

    def test_exit_0_on_real_repo_if_consistent(self) -> None:
        """Si el protocolo real del repo está consistente, el script retorna 0.
        Test guard: si esto falla, el repo tiene drift."""
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "validate_protocol_drift.py")],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        # El repo real YA está corregido por esta unidad (0158) — exit 0 esperado.
        self.assertEqual(result.returncode, 0,
                         f"validate_protocol_drift.py falló en el repo real:\n"
                         f"stdout: {result.stdout}\nstderr: {result.stderr}")
        self.assertIn("chequeos pasaron", result.stdout)


if __name__ == "__main__":
    unittest.main()
