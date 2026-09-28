"""Tests for `validate_protocol_drift.py` — motor genérico del kit.

Cobertura:
  - Caso feliz: protocolo consistente → exit 0 con 5/5 chequeos OK.
  - Drift simulado por chequeo (5 casos): cada `check_NN_*` retorna FAIL
    cuando se simula la condición de drift.
  - Los checks 10/11 leen `modelos_permitidos`/`perfiles_permitidos` de
    `.spec/protocolo-datos.yaml` (texto inyectado directo, sin depender de
    ningún archivo real) — nunca literales de modelo cableados (CA-08).
  - Ausencia de `.spec/protocolo-datos.yaml`: `main()` falla explícito
    (exit 2), no cae a un default silencioso.
"""
from __future__ import annotations

import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import validate_protocol_drift as vpd

REPO_ROOT = vpd.REPO_ROOT

#: `datos_text` mínimo y consistente para los checks 10/11 — inyectado
#: directo (los checks toman el texto, no una ruta), sin depender de un
#: archivo real en disco.
CONSISTENT_DATOS_TEXT = (
    "modelos_permitidos:\n  - opus\n  - sonnet\n  - haiku\n\n"
    "perfiles_permitidos:\n  - ligero\n  - estandar\n  - profundo\n"
)


class _TmpProtocol:
    """Crea un árbol mínimo del protocolo SDD en un directorio temporal y
    ejecuta los chequeos contra él. Cada test invoca `_set(...)` con el
    contenido exacto de cada archivo vigilado."""

    #: Las skills que invocan subagentes (checks 10 y 12): deben contener
    #: `subagent_type` o `Agent(` para que `check_12` las cuente (mismo
    #: criterio del motor, no una lista cerrada por nombre).
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
        (tmp / ".agents" / "skills").mkdir(parents=True)
        (tmp / ".spec").mkdir(parents=True, exist_ok=True)

    def _write(self, rel: str, content: str) -> None:
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def set_consistent(self) -> None:
        # 9 agents en lista permitida (check 02).
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
        # README con tabla golden (check 01).
        self._write(".spec/README.md",
                    "## Gates de validación\n\n"
                    "| Tier | Críticos | Iter | Adv |\n|---|---|---|---|\n"
                    "| bajo | 1 | 1 | no |\n| medio | 1 | 1 | no |\n| alto | 2 | 2 | sí |\n\n## Otros\n")
        # perfiles.yaml: `estandar` sin nada fuera de lo permitido (check 11).
        self._write(".spec/perfiles.yaml",
                    "default: estandar\n\nperfiles:\n  estandar:\n"
                    "    roles:\n      sdd-critico-profundo: {modelo: sonnet, effort: high}\n"
                    "    implementador_complejo: {modelo: sonnet, effort: xhigh}\n\n"
                    "  ligero:\n"
                    "    roles:\n      sdd-critico-profundo: {modelo: haiku}\n\n"
                    "  profundo:\n"
                    "    roles:\n      sdd-critico-profundo: {modelo: opus}\n")
        # `.spec/protocolo-datos.yaml` — lo que `main()` resuelve por defecto.
        self._write(".spec/protocolo-datos.yaml", CONSISTENT_DATOS_TEXT)
        # Las 6 skills de fase invocan subagentes (Agent()) y citan el
        # resolvedor (check 12); ninguna repite modelo/effort en prosa
        # (check 10).
        for name in self.PHASE_SKILLS:
            self._write(f".agents/skills/{name}/SKILL.md",
                        "Invoca Agent({subagent_type: 'sdd-especificar-redactor'}).\n"
                        "`effort_profile.py resolve --role <rol>`\n")


class HappyPathTests(unittest.TestCase):
    """Caso feliz: protocolo consistente → 5/5 OK, exit 0."""

    def setUp(self) -> None:
        import tempfile
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.proto = _TmpProtocol(self.tmp)
        self.proto.set_consistent()
        self._patches = []
        for attr in ("REPO_ROOT", "AGENTS_DIR", "README", "PERFILES_YAML", "SKILLS_DIR"):
            original = getattr(vpd, attr)
            self._patches.append((vpd, attr, original))
        vpd.REPO_ROOT = self.tmp
        vpd.AGENTS_DIR = self.tmp / ".claude" / "agents"
        vpd.README = self.tmp / ".spec" / "README.md"
        vpd.PERFILES_YAML = self.tmp / ".spec" / "perfiles.yaml"
        vpd.SKILLS_DIR = self.tmp / ".agents" / "skills"

    def tearDown(self) -> None:
        for obj, attr, original in self._patches:
            setattr(obj, attr, original)
        self.tmp_ctx.cleanup()

    def test_main_returns_0_when_protocol_consistent(self) -> None:
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
        self.assertIn("5/5 chequeos pasaron", output)
        for cid in ("01", "02", "10", "11", "12"):
            self.assertIn(f"OK: {cid}", output)

    def test_main_fails_explicit_when_protocolo_datos_missing(self) -> None:
        """Sin `.spec/protocolo-datos.yaml`, el motor falla explícito (exit 2),
        nunca con un default silencioso de modelos/perfiles permitidos."""
        (self.tmp / ".spec" / "protocolo-datos.yaml").unlink()
        buf = io.StringIO()
        original_argv = sys.argv
        sys.argv = [original_argv[0]]
        try:
            with redirect_stdout(buf):
                rc = vpd.main()
        finally:
            sys.argv = original_argv
        self.assertEqual(rc, 2)


class DriftSimulationTests(unittest.TestCase):
    """Drift simulado: los 5 chequeos deben retornar FAIL ante su condición."""

    def setUp(self) -> None:
        import tempfile
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.proto = _TmpProtocol(self.tmp)
        self.proto.set_consistent()
        self._patches = []
        for attr in ("REPO_ROOT", "AGENTS_DIR", "README", "PERFILES_YAML", "SKILLS_DIR"):
            original = getattr(vpd, attr)
            self._patches.append((vpd, attr, original))
        vpd.REPO_ROOT = self.tmp
        vpd.AGENTS_DIR = self.tmp / ".claude" / "agents"
        vpd.README = self.tmp / ".spec" / "README.md"
        vpd.PERFILES_YAML = self.tmp / ".spec" / "perfiles.yaml"
        vpd.SKILLS_DIR = self.tmp / ".agents" / "skills"

    def tearDown(self) -> None:
        for obj, attr, original in self._patches:
            setattr(obj, attr, original)
        self.tmp_ctx.cleanup()

    def test_01_readme_gates_table_drift(self) -> None:
        vpd.README.write_text(
            "## Gates de validación\n\n"
            "| Tier | Críticos | Iter | Adv |\n|---|---|---|---|\n"
            "| bajo | 1 | 1 | no |\n| medio | 2 | 2 | no |\n| alto | 3 | 3 | sí |\n\n",
            encoding="utf-8")
        ok, _ = vpd.check_01_readme_gates_table()
        self.assertFalse(ok)

    def test_02_agents_models_drift(self) -> None:
        (self.tmp / ".claude/agents" / "sdd-especificar-redactor.md").write_text(
            "---\nmodel: fable\neffort: high\n---\n", encoding="utf-8")
        ok, detail = vpd.check_02_agents_models()
        self.assertFalse(ok)
        self.assertIn("fable", detail)

    def test_10_prosa_modelo_fuera_de_lo_permitido(self) -> None:
        """Drift del chequeo 10: una skill de fase menciona un modelo que
        `modelos_permitidos` no declara — sin ese modelo cableado en el
        código del motor, solo en el `datos_text` inyectado."""
        (self.tmp / ".agents" / "skills" / "sdd-especificar").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".agents" / "skills" / "sdd-especificar" / "SKILL.md").write_text(
            "Invocar (Fable, effort medium) para el rol.\n",
            encoding="utf-8")
        ok, detail = vpd.check_10_skills_prosa_no_redundante(CONSISTENT_DATOS_TEXT)
        self.assertFalse(ok)
        self.assertIn("Fable", detail)

    def test_10_patron_subagente_con_agent_declarado(self) -> None:
        """Drift del chequeo 10: patrón `subagente `X` (Modelo, effort Y)` cuando
        existe `.claude/agents/X.md` con su propio frontmatter — la prosa debe
        referenciar al agent, no duplicarlo (independiente de la lista de
        modelos permitidos: aplica aunque el modelo mencionado sí esté
        permitido)."""
        (self.tmp / ".agents" / "skills" / "sdd-orquestar").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".agents" / "skills" / "sdd-orquestar" / "SKILL.md").write_text(
            "delegar al subagente `sdd-especificar-redactor` (sonnet, effort high)\n",
            encoding="utf-8")
        ok, detail = vpd.check_10_skills_prosa_no_redundante(CONSISTENT_DATOS_TEXT)
        self.assertFalse(ok)
        self.assertIn("sdd-especificar-redactor", detail)
        self.assertIn("duplica frontmatter", detail)

    def test_11_perfiles_yaml_missing(self) -> None:
        vpd.PERFILES_YAML.unlink()
        ok, detail = vpd.check_11_perfiles_yaml_dentro_de_lo_permitido(CONSISTENT_DATOS_TEXT)
        self.assertFalse(ok)
        self.assertIn("no existe", detail)

    def test_11_perfiles_yaml_estandar_declares_model_outside_allowed(self) -> None:
        vpd.PERFILES_YAML.write_text(
            "default: estandar\n\nperfiles:\n  estandar:\n"
            "    roles:\n      sdd-especificar-redactor: {modelo: fable, effort: high}\n"
            "    implementador_complejo: {modelo: sonnet, effort: xhigh}\n",
            encoding="utf-8")
        ok, detail = vpd.check_11_perfiles_yaml_dentro_de_lo_permitido(CONSISTENT_DATOS_TEXT)
        self.assertFalse(ok)
        self.assertIn("fable", detail)

    def test_11_perfiles_yaml_declares_profile_outside_allowed(self) -> None:
        """Un perfil top-level fuera de `perfiles_permitidos` también dispara
        FAIL — generaliza más allá de solo mirar el bloque `estandar`."""
        vpd.PERFILES_YAML.write_text(
            "default: estandar\n\nperfiles:\n  estandar:\n"
            "    roles:\n      sdd-critico-profundo: {modelo: sonnet, effort: high}\n\n"
            "  experimental:\n"
            "    roles:\n      sdd-critico-profundo: {modelo: sonnet}\n",
            encoding="utf-8")
        ok, detail = vpd.check_11_perfiles_yaml_dentro_de_lo_permitido(CONSISTENT_DATOS_TEXT)
        self.assertFalse(ok)
        self.assertIn("experimental", detail)

    def test_12_a_phase_skill_stops_citing_the_resolver(self) -> None:
        (self.tmp / ".agents" / "skills" / "sdd-tareas" / "SKILL.md").write_text(
            "Invoca Agent({subagent_type: 'sdd-tareas-redactor'}).\n",
            encoding="utf-8")
        ok, detail = vpd.check_12_skills_citan_effort_profile_resolve()
        self.assertFalse(ok)
        self.assertIn("sdd-tareas/SKILL.md", detail)

    def test_12_a_skill_that_never_invokes_a_subagent_is_not_checked(self) -> None:
        """Una skill que ni siquiera invoca subagentes no cuenta contra el
        chequeo — a diferencia del validador de origen (lista cerrada de "6
        skills de fase"), este motor detecta la invocación misma."""
        (self.tmp / ".agents" / "skills" / "sdd-perfil").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".agents" / "skills" / "sdd-perfil" / "SKILL.md").write_text(
            "No invoca subagentes.\n", encoding="utf-8")
        ok, _ = vpd.check_12_skills_citan_effort_profile_resolve()
        self.assertTrue(ok)


class ExecutableFromBashTests(unittest.TestCase):
    """El script debe ser ejecutable como `python3 validate_protocol_drift.py`
    y respetar exit codes — corrido contra un árbol sintético consistente
    propio (no contra el repositorio real que lo hospeda: ese repositorio
    puede o no tener aún el resto de la carga del kit — `.spec/README.md`,
    `.agents/skills/`, etc. — completa en el momento en que esta suite
    corre, y este test no depende de ese orden)."""

    def test_exit_0_on_a_consistent_synthetic_tree(self) -> None:
        import shutil
        import tempfile
        tmp_ctx = tempfile.TemporaryDirectory()
        try:
            tmp = Path(tmp_ctx.name)
            (tmp / ".spec" / "scripts").mkdir(parents=True)
            for name in ("_common.py", "validate_protocol_drift.py"):
                shutil.copy(REPO_ROOT / ".spec" / "scripts" / name, tmp / ".spec" / "scripts" / name)
            proto = _TmpProtocol(tmp)
            proto.set_consistent()
            result = subprocess.run(
                [sys.executable, str(tmp / ".spec" / "scripts" / "validate_protocol_drift.py")],
                capture_output=True, text=True, cwd=tmp,
            )
            self.assertEqual(result.returncode, 0,
                             f"validate_protocol_drift.py falló sobre un árbol consistente:\n"
                             f"stdout: {result.stdout}\nstderr: {result.stderr}")
            self.assertIn("chequeos pasaron", result.stdout)
        finally:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    unittest.main()
