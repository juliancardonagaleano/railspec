"""Tests for `.spec/scripts/validate_gate_budget.py` (CA-20).

Covers: a phase within its profile/tier budget exits 0, a phase that exceeds
it exits != 0 with the `presupuesto-excedido <fase>: ...` message, a unit
with no `gates` (or `gates: {}`) exits 0, and an `escalado` verdict without
`rehabilitado_por` only prints a notice — it never fails the run on its own.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS_DIR / "validate_gate_budget.py"

SAMPLE_PROFILES = """default: estandar

perfiles:
  estandar:
    roles:
      sdd-critico-cumplimiento: {modelo: sonnet, effort: high}
      sdd-critico-estructural: {modelo: haiku}
      sdd-critico-profundo: {modelo: sonnet, effort: high}
      sdd-especificar-redactor: {modelo: sonnet, effort: high}
      sdd-explorador: {modelo: sonnet, effort: medium}
      sdd-implementador: {modelo: sonnet, effort: high}
      sdd-planificar-redactor: {modelo: sonnet, effort: high}
      sdd-refutador: {modelo: sonnet, effort: high}
      sdd-tareas-redactor: {modelo: haiku}
    gate:
      bajo: {criticos: 1, iteraciones: 1, adversarial: false}
      medio: {criticos: 1, iteraciones: 1, adversarial: false}
      alto: {criticos: 2, iteraciones: 2, adversarial: true}
    exploradores:
      bajo: 1
      medio: 1
      alto: 3
    implementador_complejo: {modelo: sonnet, effort: xhigh}
"""


def _run(unit_dir: Path, profiles_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--unit", str(unit_dir), "--profiles", str(profiles_path)],
        capture_output=True,
        text=True,
        check=False,
    )


class ValidateGateBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.profiles_path = self.tmp_path / "perfiles.yaml"
        self.profiles_path.write_text(SAMPLE_PROFILES, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_unit(self, name: str, estado_body: str) -> Path:
        unit_dir = self.tmp_path / name
        unit_dir.mkdir()
        (unit_dir / "_estado.yaml").write_text(
            f"id: {name}\ntitulo: fixture\nfase: implement\nestado: en-progreso\n"
            f"perfil: estandar\nriesgo: alto\n{estado_body}",
            encoding="utf-8",
        )
        return unit_dir

    def test_within_budget_exits_zero(self) -> None:
        unit_dir = self._write_unit(
            "within",
            "gates:\n  spec:\n    veredicto: refinado\n    iteraciones: 2\n",
        )
        result = _run(unit_dir, self.profiles_path)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exceeds_budget_exits_nonzero_with_message(self) -> None:
        unit_dir = self._write_unit(
            "exceeds",
            "gates:\n  spec:\n    veredicto: escalado\n    iteraciones: 3\n"
            "    rehabilitado_por: {por: julian, en: '2026-09-24T10:00:00Z', motivo: nueva-info}\n",
        )
        result = _run(unit_dir, self.profiles_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "presupuesto-excedido spec: iteraciones=3 > tope=2 (estandar, alto)",
            result.stderr,
        )

    def test_no_gates_field_exits_zero(self) -> None:
        unit_dir = self._write_unit("no-gates", "")
        result = _run(unit_dir, self.profiles_path)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_inline_empty_gates_exits_zero(self) -> None:
        unit_dir = self._write_unit("empty-gates", "gates: {}\n")
        result = _run(unit_dir, self.profiles_path)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_escalado_without_rehabilitado_por_warns_but_passes(self) -> None:
        unit_dir = self._write_unit(
            "escalado-sin-rehabilitar",
            "gates:\n  spec:\n    veredicto: escalado\n    iteraciones: 1\n",
        )
        result = _run(unit_dir, self.profiles_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("escalado sin `rehabilitado_por`", result.stdout)

    def test_escalado_without_iteraciones_warns_both_and_passes(self) -> None:
        unit_dir = self._write_unit(
            "escalado-sin-iteraciones",
            "gates:\n  spec:\n    veredicto: escalado\n",
        )
        result = _run(unit_dir, self.profiles_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("iteraciones-ausente spec", result.stdout)
        self.assertIn("escalado sin `rehabilitado_por`", result.stdout)

    def test_multiple_phases_only_reports_the_one_over_budget(self) -> None:
        unit_dir = self._write_unit(
            "multi-phase",
            "gates:\n"
            "  spec:\n    veredicto: refinado\n    iteraciones: 2\n"
            "  plan:\n    veredicto: refinado\n    iteraciones: 3\n",
        )
        result = _run(unit_dir, self.profiles_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("presupuesto-excedido spec", result.stderr)
        self.assertIn("presupuesto-excedido plan: iteraciones=3 > tope=2", result.stderr)

    def test_missing_state_file_exits_nonzero(self) -> None:
        unit_dir = self.tmp_path / "missing"
        unit_dir.mkdir()
        result = _run(unit_dir, self.profiles_path)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
