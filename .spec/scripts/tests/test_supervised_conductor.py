"""`supervised_conductor.py` — CA-01..CA-08 (unit 0118, G1).

Every case runs the real script as a subprocess (`python3 supervised_conductor.py
run …`), against a synthetic `claude` stand-in (a small, chmod'd Python script)
substituted via `--claude-bin`, over a minimal isolated-unit mandate built fresh
in a temporary directory — never against a real `claude -p` invocation and
never by writing under `.spec/_fixtures/` (out of scope for this group's
`archivos:`).

The stub coordinates with the test through two files the test points it at via
environment variables (`HOC_ESTADO_FILE`, `HOC_COUNTER_FILE`) and a `HOC_MODE`
switch (`advance` | `noop` | `hang`) — the conductor's own subprocess call
inherits the parent environment (it does not pass an explicit `env=`), so
setting `os.environ` before invoking the conductor is enough.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parents[1]
SCRIPT = SCRIPTS / "supervised_conductor.py"
INSTANCE_LOCK = SCRIPTS / "instance_lock.py"

sys.path.insert(0, str(SCRIPTS))


# ======================================================================================
# Minimal isolated-unit mandate — only the sections this script's own code paths read
# (`## Estado`, `## Instancia en curso`, `## Punto de retoma`, `## Aprobación`).
# ======================================================================================

MANDATE_TEMPLATE = """# Mandato supervised — fixture de prueba (0118, G1)

## Estado

- estado: {estado}

## Instancia en curso

Sin instancia.

## Punto de retoma

{retoma}

## Aprobación

Vacía.
"""

ESTADO_TEMPLATE = """id: 9900-fixture-conductor
titulo: "Fixture del conductor (0118)"
dueño: "Test"
carril: "sdd"
fase: {fase}
estado: en-progreso
creado: "2026-09-20T00:00:00Z"
actualizado: "2026-09-20T00:00:00Z"
governance_refs: []
comando_validacion: "true"
modo: supervisado
mandato: mandato.md
riesgo: alto
gates: {{}}
validaciones_mandato: []
"""

STUB_SOURCE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import os
    import subprocess
    import sys
    import time

    mode = os.environ.get("HOC_MODE", "noop")

    if mode == "hang":
        lock_mandate = os.environ.get("HOC_LOCK_MANDATE")
        if lock_mandate:
            subprocess.run(
                [sys.executable, os.environ["HOC_LOCK_SCRIPT"], "acquire", lock_mandate,
                 "--session", os.environ.get("HOC_LOCK_SESSION", "hijo"),
                 "--launcher", os.environ.get("HOC_LOCK_LAUNCHER", "Test")],
                check=False)
        time.sleep(30)
        sys.exit(0)

    if mode == "noop":
        sys.exit(0)

    if mode == "advance":
        counter_file = os.environ["HOC_COUNTER_FILE"]
        estado_file = os.environ["HOC_ESTADO_FILE"]
        fases = os.environ["HOC_FASES"].split(",")
        n = 0
        if os.path.exists(counter_file):
            n = int(open(counter_file).read().strip() or "0")
        fase = fases[min(n, len(fases) - 1)]
        n += 1
        open(counter_file, "w").write(str(n))
        text = open(estado_file).read()
        lines = text.splitlines()
        out = []
        for line in lines:
            if line.startswith("fase:"):
                out.append(f"fase: {fase}")
            else:
                out.append(line)
        open(estado_file, "w").write("\\n".join(out) + "\\n")
        sys.exit(0)

    sys.exit(1)
""")


def make_stub(tmp: Path) -> Path:
    stub = tmp / "claude-stub.py"
    stub.write_text(STUB_SOURCE, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def make_unit(tmp: Path, *, estado: str = "parado", retoma: str = "Sin entradas.",
              fase: str = "plan", bitacora: str | None = None) -> Path:
    unit_dir = tmp / "unidad"
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "mandato.md").write_text(
        MANDATE_TEMPLATE.format(estado=estado, retoma=retoma), encoding="utf-8")
    (unit_dir / "_estado.yaml").write_text(
        ESTADO_TEMPLATE.format(fase=fase), encoding="utf-8")
    if bitacora is not None:
        (unit_dir / "bitacora.md").write_text(bitacora, encoding="utf-8")
    return unit_dir


def run_conductor(unit_dir: Path, evidencia: Path, stub: Path, extra: list[str],
                  env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_overrides)
    args = [sys.executable, str(SCRIPT), "run", "--unidad", str(unit_dir),
             "--evidencia-dir", str(evidencia), "--claude-bin", str(stub),
             *extra]
    return subprocess.run(args, capture_output=True, text=True, check=False, env=env)


class ConductorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hoc-test-")
        self.tmp = Path(self._tmp.name)
        self.stub = make_stub(self.tmp)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestAdvanceAndFinish(ConductorTestCase):
    """CA-01/02/03/04/11: avanza N pasos (uno por invocación fresca) y para en un
    estado terminal, con un PID/proceso nuevo por paso."""

    def test_advances_three_steps_then_stops_clean(self) -> None:
        unit_dir = make_unit(self.tmp, estado="parado", fase="research")
        evidencia = self.tmp / "evidencia"
        counter_file = self.tmp / "counter.txt"
        result = run_conductor(
            unit_dir, evidencia, self.stub,
            ["--max-pasos", "10", "--tope-estancamiento", "10"],
            {
                "HOC_MODE": "advance",
                "HOC_COUNTER_FILE": str(counter_file),
                "HOC_ESTADO_FILE": str(unit_dir / "_estado.yaml"),
                "HOC_FASES": "plan,tasks,done",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # Three fresh invocations, one per step (CA-11: distinct processes/log files).
        for n in (1, 2, 3):
            self.assertTrue((evidencia / f"{n}.log").is_file(), f"falta log del paso {n}")
            exit_code_text = (evidencia / f"{n}.exit-code.txt").read_text(encoding="utf-8")
            self.assertTrue(exit_code_text.startswith("0\n"), exit_code_text)
            self.assertIn("motivo: fin", exit_code_text)
        self.assertFalse((evidencia / "4.log").exists())
        final_state = (unit_dir / "_estado.yaml").read_text(encoding="utf-8")
        self.assertIn("fase: done", final_state)

    def test_advances_despite_unresolved_retoma_when_unit_made_real_progress(self) -> None:
        """Regresión H6 (gate de código 0118, piloto real 2026-09-21):
        `validate_mandate.py --resumen` reporta «retoma sin punto verificado»
        para CUALQUIER mandato `aprobado` que nunca escribió `## Punto de
        retoma` (esa sección solo se llena al parar) — verdadero incluso tras
        un paso recién terminado con éxito y avance real. Antes del fix, el
        bucle paraba en el paso 1 pese a `avanzo: si`; ahora debe encadenar
        los 3 pasos igual que `test_advances_three_steps_then_stops_clean`,
        con la única diferencia de partir de `estado: aprobado`/retoma vacía
        (el caso más común: un mandato recién aprobado que nunca se pausó)."""
        unit_dir = make_unit(self.tmp, estado="aprobado", retoma="Sin entradas.", fase="research")
        evidencia = self.tmp / "evidencia"
        counter_file = self.tmp / "counter.txt"
        result = run_conductor(
            unit_dir, evidencia, self.stub,
            ["--max-pasos", "10", "--tope-estancamiento", "10"],
            {
                "HOC_MODE": "advance",
                "HOC_COUNTER_FILE": str(counter_file),
                "HOC_ESTADO_FILE": str(unit_dir / "_estado.yaml"),
                "HOC_FASES": "plan,tasks,done",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for n in (1, 2, 3):
            self.assertTrue((evidencia / f"{n}.log").is_file(), f"falta log del paso {n}")
        self.assertFalse((evidencia / "4.log").exists())
        final_state = (unit_dir / "_estado.yaml").read_text(encoding="utf-8")
        self.assertIn("fase: done", final_state)
        log_text = (evidencia / "conductor.log").read_text(encoding="utf-8")
        self.assertNotIn("retoma sin punto verificado» y la unidad no avanzó", log_text)

    def test_stops_when_retoma_is_stale_even_with_real_progress(self) -> None:
        """Regresión hallazgo alta (gate de código 0118, 2026-09-21, sostenido por
        sdd-refutador): a diferencia del caso H6 de arriba (retoma NUNCA
        escrito), acá `## Punto de retoma` SÍ tiene una entrada, pero es más
        vieja que la última entrada de `bitacora.md` -- una divergencia real
        (plan.md § Riesgo 3), detectada por
        `validate_mandate.resume_divergence_kind` == "stale" e informada con
        la línea separada `STALE_RESUME_LINE`. Fixture equivalente:
        `.spec/_fixtures/supervised/unidad-aislada-retoma-antigua`. `avanzo`
        (firma fase+gates) es ciego a esta divergencia -- por eso el bucle
        debe parar aunque HOC_MODE=advance haga avanzar la fase de verdad,
        a diferencia del caso H6 puro."""
        unit_dir = make_unit(
            self.tmp, estado="aprobado", fase="research",
            retoma="### 2026-09-16T12:00Z\n\n- nota: retoma registrada acá.\n",
            bitacora=(
                "# Bitácora\n\n"
                "## 2026-09-16T12:00Z · fase: research\n\n"
                "**Qué hice:** se registró el punto de retoma.\n\n"
                "## 2026-09-17T10:00Z · fase: research\n\n"
                "**Qué hice:** se avanzó sin volver a registrar el punto de retoma.\n"
            ),
        )
        evidencia = self.tmp / "evidencia"
        counter_file = self.tmp / "counter.txt"
        result = run_conductor(
            unit_dir, evidencia, self.stub,
            ["--max-pasos", "10", "--tope-estancamiento", "10"],
            {
                "HOC_MODE": "advance",
                "HOC_COUNTER_FILE": str(counter_file),
                "HOC_ESTADO_FILE": str(unit_dir / "_estado.yaml"),
                "HOC_FASES": "plan,tasks,done",
            },
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue((evidencia / "1.log").is_file())
        self.assertFalse((evidencia / "2.log").exists())
        log_text = (evidencia / "conductor.log").read_text(encoding="utf-8")
        self.assertIn("retoma desactualizada respecto de bitácora", log_text)


class TestHardClockCap(ConductorTestCase):
    """CA-06/07: cota de reloj agotada dentro de un paso ⇒ motivo `cota-agotada`,
    árbol resumible (el lock, nunca tocado por el conductor, sigue libre)."""

    def test_hard_cap_kills_hanging_invocation(self) -> None:
        unit_dir = make_unit(self.tmp, estado="parado", fase="plan")
        evidencia = self.tmp / "evidencia"
        result = run_conductor(
            unit_dir, evidencia, self.stub,
            ["--cota-blanda", "1", "--margen-duro", "1"],
            {"HOC_MODE": "hang"},
        )
        self.assertEqual(result.returncode, 142, result.stderr)
        exit_code_text = (evidencia / "1.exit-code.txt").read_text(encoding="utf-8")
        self.assertIn("motivo: cota-agotada", exit_code_text)
        # Nothing acquired the lock during this run — it stays free, i.e. resumable
        # without human intervention (CA-07).
        inspect = subprocess.run(
            [sys.executable, str(INSTANCE_LOCK), "inspect", str(unit_dir / "mandato.md")],
            capture_output=True, text=True, check=False)
        self.assertEqual(inspect.returncode, 0)
        self.assertEqual(inspect.stdout.strip(), "libre")

    def test_hard_cap_with_lock_left_occupied_reports_orphan_and_stops(self) -> None:
        """Simulates a child that was mid-turn (held the lock) when the hard cap
        killed it: the lock comes back occupied, and the conductor must report a
        potential orphan and stop — never release or relaunch on its own (§ 1.1)."""
        unit_dir = make_unit(self.tmp, estado="parado", fase="plan")
        evidencia = self.tmp / "evidencia"
        result = run_conductor(
            unit_dir, evidencia, self.stub,
            ["--cota-blanda", "1", "--margen-duro", "1"],
            {
                "HOC_MODE": "hang",
                "HOC_LOCK_MANDATE": str(unit_dir / "mandato.md"),
                "HOC_LOCK_SCRIPT": str(INSTANCE_LOCK),
                "HOC_LOCK_SESSION": "sesion-hijo",
                "HOC_LOCK_LAUNCHER": "Test",
            },
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        log_text = (evidencia / "conductor.log").read_text(encoding="utf-8")
        self.assertIn("cota-agotada-lock-huerfano", log_text)


class TestLockBusyAtStart(ConductorTestCase):
    """CA-05/CA-13: lock ocupado por otra sesión al decidir lanzar ⇒ parada, sin
    lanzar ninguna invocación (no error) — el conductor solo llama `inspect`."""

    def test_stops_without_launching_when_lock_busy(self) -> None:
        unit_dir = make_unit(self.tmp, estado="parado", fase="plan")
        acquire = subprocess.run(
            [sys.executable, str(INSTANCE_LOCK), "acquire", str(unit_dir / "mandato.md"),
             "--session", "otra-sesion", "--launcher", "Otro"],
            capture_output=True, text=True, check=False)
        self.assertEqual(acquire.returncode, 0, acquire.stderr)
        evidencia = self.tmp / "evidencia"
        result = run_conductor(
            unit_dir, evidencia, self.stub, [], {"HOC_MODE": "noop"})
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertFalse((evidencia / "1.log").exists())
        self.assertFalse((evidencia / "1.exit-code.txt").exists())


class TestStallDetection(ConductorTestCase):
    """Decisiones de diseño (Riesgo 6): estancamiento — `k` invocaciones seguidas
    sin cambio de firma (fase+gates) de la unidad en curso ⇒ parada tipificada,
    nunca reintento silencioso."""

    def test_stops_after_stall_limit_with_no_signature_change(self) -> None:
        unit_dir = make_unit(self.tmp, estado="parado", fase="plan")
        evidencia = self.tmp / "evidencia"
        result = run_conductor(
            unit_dir, evidencia, self.stub,
            ["--tope-estancamiento", "2", "--max-pasos", "20"],
            {"HOC_MODE": "noop"},
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue((evidencia / "2.log").is_file())
        self.assertFalse((evidencia / "3.log").exists())
        log_text = (evidencia / "conductor.log").read_text(encoding="utf-8")
        self.assertIn("estancamiento", log_text)


class TestUnresolvedResume(ConductorTestCase):
    """CA-05/Riesgo 3: `validate_mandate.py --resumen` reportando la línea literal
    `retoma sin punto verificado` es señal de parada, nunca de avanzar a ciegas."""

    def test_stops_when_resumen_reports_unresolved_retoma(self) -> None:
        unit_dir = make_unit(self.tmp, estado="aprobado", retoma="Sin entradas.", fase="plan")
        evidencia = self.tmp / "evidencia"
        result = run_conductor(
            unit_dir, evidencia, self.stub,
            ["--max-pasos", "20"],
            {"HOC_MODE": "noop"},
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue((evidencia / "1.log").is_file())
        self.assertFalse((evidencia / "2.log").exists())
        log_text = (evidencia / "conductor.log").read_text(encoding="utf-8")
        self.assertIn("retoma sin punto verificado", log_text)


class TestUnambiguousReference(unittest.TestCase):
    """Regresión H7 (gate de código 0118, piloto real 2026-09-21):
    `Mandate.reference` para la forma `unidad` es el valor LITERAL del campo
    `mandato:` (típicamente `mandato.md`, igual en cualquier unidad
    aislada del backlog) — no un identificador que distinga esa unidad de
    cualquier otra. `unambiguous_reference` debe usar en su lugar la ruta
    resuelta (`Mandate.path`) para esa forma, y seguir usando `reference`
    (ya único, un id de plan) para la forma `plan`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hoc-ref-test-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unidad_form_uses_the_resolved_path_not_the_literal_field(self) -> None:
        import supervised_conductor as hoc
        import validate_mandate as vm

        unit_dir = self.tmp / "units" / "9900-x"
        unit_dir.mkdir(parents=True)
        supervised = unit_dir / "mandato.md"
        supervised.write_text("# fixture\n", encoding="utf-8")
        mandate = vm.Mandate(supervised, "unidad", "mandato.md", self.tmp / "units")
        reference = hoc.unambiguous_reference(mandate)
        self.assertNotEqual(reference, "mandato.md")
        self.assertEqual(reference, str(supervised))

    def test_plan_form_keeps_the_already_unique_plan_id(self) -> None:
        import supervised_conductor as hoc
        import validate_mandate as vm

        plan_dir = self.tmp / "planes" / "mi-plan"
        plan_dir.mkdir(parents=True)
        plan_file = plan_dir / "plan-maestro.md"
        plan_file.write_text("# fixture\n", encoding="utf-8")
        mandate = vm.Mandate(plan_file, "plan", "mi-plan", plan_dir / "units")
        self.assertEqual(hoc.unambiguous_reference(mandate), "mi-plan")


class TestExitCodeFileShape(ConductorTestCase):
    """CA-08: por invocación, un archivo inspeccionable sin parsear el log."""

    def test_exit_code_file_has_five_lines_with_machine_readable_first_line(self) -> None:
        unit_dir = make_unit(self.tmp, estado="parado", fase="plan")
        evidencia = self.tmp / "evidencia"
        run_conductor(unit_dir, evidencia, self.stub,
                     ["--tope-estancamiento", "1"], {"HOC_MODE": "noop"})
        lines = (evidencia / "1.exit-code.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[0], "0")
        self.assertTrue(lines[1].startswith("motivo: "))
        self.assertTrue(lines[2].startswith("duracion-s: "))
        self.assertTrue(lines[3].startswith("avanzo: "))
        self.assertTrue(lines[4].startswith("estancamiento-consecutivo: "))


if __name__ == "__main__":
    unittest.main()
