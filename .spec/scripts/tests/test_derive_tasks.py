"""`derive_tasks.py` — deterministic derivation of `tasks.md` (unit 0116, G2).

Covers CA-07a (zero-model-call generation when `plan.md` has the recognized
shape), CA-07b (the generated file's origin metadata), CA-08 (the recognized
form is absent → exit 3, drafted-path fallback, never a hard error) and CA-09
(a human edit after generation is detected and recorded, never lost silently).

Builds its own minimal `plan.md`/`spec.md` fixtures with `tempfile` rather than
adding new files under `.spec/_fixtures/`: the mechanism under test only reads
two small, self-contained inputs, and every case (recognized / not recognized
for each of the three reasons the plan.md documents) is easiest to see as a
literal string right next to its assertion.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import derive_tasks  # noqa: E402

SPEC_TEXT = """# Spec — Unidad de prueba

## Criterios de aceptación

- [ ] CA-01 — primer criterio.
- [ ] CA-02a — segundo criterio, variante a.
- [ ] CA-02b — segundo criterio, variante b.
"""

PLAN_RECOGNIZED = """# Plan técnico — Unidad de prueba

## Grupos de tareas paralelizables

| Grupo | Alcance | Archivos | Depende de | Cubre CA-NN | Complejidad |
|---|---|---|---|---|---|
| G1 | Hace la primera parte | `pkg/a.py`, `pkg/tests/test_a.py` | — | CA-01 | estandar |
| G2 | Hace la segunda parte | `pkg/b.py` | G1 | CA-02 | estandar |
"""

PLAN_NO_COLUMN = """# Plan técnico — Unidad de prueba

## Grupos de tareas paralelizables

| Grupo | Alcance | Archivos | Depende de | Complejidad |
|---|---|---|---|---|
| G1 | Hace la primera parte | `pkg/a.py` | — | estandar |
| G2 | Hace la segunda parte | `pkg/b.py` | G1 | estandar |
"""

PLAN_PLACEHOLDER_FILES = """# Plan técnico — Unidad de prueba

## Grupos de tareas paralelizables

| Grupo | Alcance | Archivos | Depende de | Cubre CA-NN | Complejidad |
|---|---|---|---|---|---|
| G1 | Hace todo | `...` | — | CA-01, CA-02 | estandar |
"""

PLAN_INCOMPLETE_COVERAGE = """# Plan técnico — Unidad de prueba

## Grupos de tareas paralelizables

| Grupo | Alcance | Archivos | Depende de | Cubre CA-NN | Complejidad |
|---|---|---|---|---|---|
| G1 | Hace solo la primera parte | `pkg/a.py` | — | CA-01 | estandar |
"""


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


class GenerateRecognizedShape(unittest.TestCase):
    """CA-07a / CA-07b."""

    def test_generates_with_zero_model_calls_and_deterministic_origin(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan = _write(tmp_path, "plan.md", PLAN_RECOGNIZED)
            spec = _write(tmp_path, "spec.md", SPEC_TEXT)
            out = tmp_path / "tasks.md"

            exit_code = derive_tasks.main(
                ["generate", "--plan", str(plan), "--spec", str(spec), "--out", str(out)]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out.is_file())
            text = out.read_text(encoding="utf-8")
            # CA-07b: origin metadata marks it deterministic.
            self.assertRegex(text, r"> origen: determinista · tasks_sha256: [0-9a-f]{64}")
            # Both groups became tasks, and CA-01/CA-02a/CA-02b are all traceable
            # (root token `CA-02` in the plan covers both lettered variants).
            self.assertIn("T1 —", text)
            self.assertIn("T2 —", text)
            self.assertIn("cubre: CA-01", text)
            self.assertIn("depende de: T1", text)

    def test_no_derivation_ever_calls_a_model(self) -> None:
        """`generate` never imports or shells out to anything model-related —
        it is pure text parsing (CA-07a's "cero llamadas a modelo", verified
        here by construction: the module has no such import at all)."""
        source = (SCRIPTS / "derive_tasks.py").read_text(encoding="utf-8")
        for forbidden in ("anthropic", "openai", "subprocess"):
            self.assertNotIn(forbidden, source)


class GenerateUnrecognizedShape(unittest.TestCase):
    """CA-08: three independent reasons the shape can fail to be recognized,
    every one of them a graceful fallback (exit 3), never a hard error."""

    def _assert_falls_back(self, plan_text: str) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan = _write(tmp_path, "plan.md", plan_text)
            spec = _write(tmp_path, "spec.md", SPEC_TEXT)
            out = tmp_path / "tasks.md"

            exit_code = derive_tasks.main(
                ["generate", "--plan", str(plan), "--spec", str(spec), "--out", str(out)]
            )

            self.assertEqual(exit_code, 3)
            self.assertFalse(out.exists())

    def test_missing_cubre_ca_column(self) -> None:
        self._assert_falls_back(PLAN_NO_COLUMN)

    def test_archivos_is_a_placeholder(self) -> None:
        self._assert_falls_back(PLAN_PLACEHOLDER_FILES)

    def test_coverage_is_not_100_percent(self) -> None:
        self._assert_falls_back(PLAN_INCOMPLETE_COVERAGE)

    def test_missing_file_is_a_real_error_not_a_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spec = _write(tmp_path, "spec.md", SPEC_TEXT)
            exit_code = derive_tasks.main(
                [
                    "generate",
                    "--plan", str(tmp_path / "no-existe.md"),
                    "--spec", str(spec),
                    "--out", str(tmp_path / "tasks.md"),
                ]
            )
            self.assertEqual(exit_code, 1)


class Verify(unittest.TestCase):
    """CA-09: a human edit after deterministic generation is detected and
    recorded — never lost silently."""

    def _generate(self, tmp_path: Path) -> Path:
        plan = _write(tmp_path, "plan.md", PLAN_RECOGNIZED)
        spec = _write(tmp_path, "spec.md", SPEC_TEXT)
        out = tmp_path / "tasks.md"
        exit_code = derive_tasks.main(
            ["generate", "--plan", str(plan), "--spec", str(spec), "--out", str(out)]
        )
        self.assertEqual(exit_code, 0)
        return out

    def test_untouched_file_verifies_clean(self) -> None:
        with TemporaryDirectory() as tmp:
            out = self._generate(Path(tmp))
            exit_code = derive_tasks.main(["verify", "--tasks", str(out)])
            self.assertEqual(exit_code, 0)
            self.assertIn("determinista ·", out.read_text(encoding="utf-8"))
            self.assertNotIn("forzado-humano", out.read_text(encoding="utf-8"))

    def test_edited_file_is_marked_forzado_humano(self) -> None:
        with TemporaryDirectory() as tmp:
            out = self._generate(Path(tmp))
            edited = out.read_text(encoding="utf-8").replace(
                "- [ ] T1", "- [x] T1"  # a human checked a box by hand
            )
            out.write_text(edited, encoding="utf-8")

            exit_code = derive_tasks.main(["verify", "--tasks", str(out)])

            self.assertEqual(exit_code, 0)  # recorded, never blocking
            text = out.read_text(encoding="utf-8")
            self.assertIn("> origen: determinista+forzado-humano · tasks_sha256:", text)

    def test_second_verify_after_forcing_stays_clean(self) -> None:
        """Once the hash is refreshed on the forced line, re-running `verify`
        with no further edits reports a match — the forcing is not re-flagged
        forever, only detected once per new edit."""
        with TemporaryDirectory() as tmp:
            out = self._generate(Path(tmp))
            out.write_text(
                out.read_text(encoding="utf-8").replace("- [ ] T1", "- [x] T1"),
                encoding="utf-8",
            )
            derive_tasks.main(["verify", "--tasks", str(out)])
            text_after_forcing = out.read_text(encoding="utf-8")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = derive_tasks.main(["verify", "--tasks", str(out)])

            self.assertEqual(exit_code, 0)
            self.assertIn("PASS", stdout.getvalue())
            # The file did not change again — the refreshed hash already matches.
            self.assertEqual(text_after_forcing, out.read_text(encoding="utf-8"))

    def test_redactado_origin_is_not_applicable(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tasks = _write(
                tmp_path,
                "tasks.md",
                "# Tareas — Unidad de prueba\n\n"
                "> origen: redactado · sdd-tareas-redactor\n\n"
                "## Pendientes\n\n- [ ] T1 — algo\n",
            )
            exit_code = derive_tasks.main(["verify", "--tasks", str(tasks)])
            self.assertEqual(exit_code, 0)

    def test_missing_file_is_an_error(self) -> None:
        with TemporaryDirectory() as tmp:
            exit_code = derive_tasks.main(
                ["verify", "--tasks", str(Path(tmp) / "no-existe.md")]
            )
            self.assertEqual(exit_code, 1)

    def test_missing_origen_line_is_an_error(self) -> None:
        with TemporaryDirectory() as tmp:
            tasks = _write(
                Path(tmp), "tasks.md", "# Tareas — Unidad de prueba\n\n## Pendientes\n"
            )
            exit_code = derive_tasks.main(["verify", "--tasks", str(tasks)])
            self.assertEqual(exit_code, 1)


class CheckPlan(unittest.TestCase):
    """`check-plan` (0124) — validación sin escribir archivo. Exit 0 si
    la tabla está en forma derivable; exit 3 si no; exit 1 si error real.
    """

    def _exit(self, plan_text: str, exit_code_expected: int) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan = _write(tmp_path, "plan.md", plan_text)
            spec = _write(tmp_path, "spec.md", SPEC_TEXT)
            exit_code = derive_tasks.main(
                ["check-plan", "--plan", str(plan), "--spec", str(spec)]
            )
            self.assertEqual(exit_code, exit_code_expected)

    def test_recognized_shape_returns_0(self) -> None:
        self._exit(PLAN_RECOGNIZED, 0)

    def test_no_cubre_column_returns_3(self) -> None:
        self._exit(PLAN_NO_COLUMN, 3)

    def test_placeholder_files_returns_3(self) -> None:
        self._exit(PLAN_PLACEHOLDER_FILES, 3)

    def test_incomplete_coverage_returns_3(self) -> None:
        self._exit(PLAN_INCOMPLETE_COVERAGE, 3)

    def test_missing_plan_is_real_error(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spec = _write(tmp_path, "spec.md", SPEC_TEXT)
            exit_code = derive_tasks.main(
                [
                    "check-plan",
                    "--plan", str(tmp_path / "no-existe.md"),
                    "--spec", str(spec),
                ]
            )
            self.assertEqual(exit_code, 1)

    def test_does_not_write_output(self) -> None:
        """`check-plan` es solo lectura — no debe crear `tasks.md` aunque la
        forma sea OK. Lo confirma el contrato de no escribir archivo."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan = _write(tmp_path, "plan.md", PLAN_RECOGNIZED)
            spec = _write(tmp_path, "spec.md", SPEC_TEXT)
            exit_code = derive_tasks.main(
                ["check-plan", "--plan", str(plan), "--spec", str(spec)]
            )
            self.assertEqual(exit_code, 0)
            # Si generara, habría escrito `tasks.md` adyacente al plan.
            self.assertFalse((tmp_path / "tasks.md").exists())


if __name__ == "__main__":
    unittest.main()
