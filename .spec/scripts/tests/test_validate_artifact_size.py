"""`validate_artifact_size.py` — per-artifact size budget validator (unit 0116, G1).

Covers CA-01 to CA-06 entirely with artifacts generated inline into a
temporary directory — no fixture tree on disk. Two cases per artifact kind
(spec/plan/bitácora): one exceeding its budget, one within it.

Runs the script as a subprocess (`sys.executable`), same pattern as
`test_mandate_anchor.py`, so what is tested is exactly the CLI a human or a
skill would invoke.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "validate_artifact_size.py"

sys.path.insert(0, str(SCRIPTS))
from validate_artifact_size import BUDGETS  # noqa: E402 — recalibrado 0116-D2


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, check=False)


def write_lines(path: Path, n: int, *, prefix: str = "línea") -> None:
    path.write_text("\n".join(f"{prefix} {i}" for i in range(1, n + 1)) + "\n",
                     encoding="utf-8")


class SpecBudgetTests(unittest.TestCase):
    """CA-01 — `spec.md` de más de BUDGETS['spec'] líneas se rechaza."""

    def test_within_budget_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "spec.md"
            write_lines(p, BUDGETS["spec"])
            result = run(["spec", str(p)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)

    def test_exceeds_budget_fails_naming_rule_and_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "spec.md"
            write_lines(p, BUDGETS["spec"] + 1)
            result = run(["spec", str(p)])
            self.assertEqual(result.returncode, 1)
            self.assertIn(str(BUDGETS["spec"] + 1), result.stdout)
            self.assertIn(str(BUDGETS["spec"]), result.stdout)
            self.assertIn("presupuesto-spec-excedido", result.stdout)


class PlanBudgetTests(unittest.TestCase):
    """CA-02 — `plan.md` de más de BUDGETS['plan'] líneas se rechaza."""

    def test_within_budget_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "plan.md"
            write_lines(p, BUDGETS["plan"])
            result = run(["plan", str(p)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exceeds_budget_fails_naming_rule_and_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "plan.md"
            write_lines(p, BUDGETS["plan"] + 1)
            result = run(["plan", str(p)])
            self.assertEqual(result.returncode, 1)
            self.assertIn(str(BUDGETS["plan"] + 1), result.stdout)
            self.assertIn(str(BUDGETS["plan"]), result.stdout)
            self.assertIn("presupuesto-plan-excedido", result.stdout)


class BitacoraBudgetTests(unittest.TestCase):
    """CA-03 — entrada de bitácora (bloque de nivel 2) de más de
    BUDGETS['bitacora'] líneas."""

    def test_entry_within_budget_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bitacora.md"
            body = "\n".join(f"línea {i}" for i in range(1, BUDGETS["bitacora"]))
            p.write_text(f"## 2026-09-20T00:00Z · entrada corta\n{body}\n",
                         encoding="utf-8")
            result = run(["bitacora", str(p)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_entry_exceeding_budget_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bitacora.md"
            body = "\n".join(f"línea {i}" for i in range(1, BUDGETS["bitacora"] + 1))
            p.write_text(f"## 2026-09-20T00:00Z · entrada larga\n{body}\n",
                         encoding="utf-8")
            result = run(["bitacora", str(p)])
            self.assertEqual(result.returncode, 1)
            self.assertIn("presupuesto-bitacora-excedido", result.stdout)

    def test_nested_level3_header_does_not_close_the_entry(self) -> None:
        """CA-03: un `### ` anidado no cierra la entrada de nivel 2."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bitacora.md"
            half = BUDGETS["bitacora"] // 2
            first_block = "\n".join(f"línea {i}" for i in range(1, half + 5))
            second_block = "\n".join(f"línea {i}" for i in range(1, half + 20))
            p.write_text(
                "## 2026-09-20T00:00Z · entrada con subsección\n"
                f"{first_block}\n"
                "### subsección anidada\n"
                f"{second_block}\n",
                encoding="utf-8",
            )
            result = run(["bitacora", str(p)])
            # 1 (header) + (half+4) + 1 (### header) + (half+19) > BUDGETS: fails as ONE entry.
            self.assertEqual(result.returncode, 1)
            self.assertIn("presupuesto-bitacora-excedido", result.stdout)
            # Only one entry counted (no second FAIL line for a "### " entry).
            self.assertEqual(result.stdout.count("presupuesto-bitacora-excedido"), 2)

    def test_citation_entry_passes(self) -> None:
        """CA-04 — cita un id externo en vez de copiar; no requiere trato
        especial: una entrada que cita es corta por construcción y por eso
        cabe en el presupuesto igual que cualquier otra entrada corta."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bitacora.md"
            p.write_text(
                "## 2026-09-24T00:00Z · gate: codigo · veredicto: aprobado\n"
                "cita: transcripción completa en "
                "`evidencia/2026-09-24-gate.jsonl`.\n",
                encoding="utf-8",
            )
            result = run(["bitacora", str(p)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)


class SyntheticOversizeArtifactTests(unittest.TestCase):
    """CA-05 — un artefacto sobredimensionado se rechaza con el mensaje del
    presupuesto excedido y el conteo real; uno dentro del presupuesto pasa.
    Generados inline en un directorio temporal (D-14: sin fixture de larga
    vida en disco)."""

    def test_spec_rejected_with_real_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "spec.md"
            write_lines(p, BUDGETS["spec"] + 57)
            result = run(["spec", str(p)])
            self.assertEqual(result.returncode, 1)
            self.assertIn("presupuesto-spec-excedido", result.stdout)
            self.assertIn(str(BUDGETS["spec"] + 57), result.stdout)
            self.assertIn(str(BUDGETS["spec"]), result.stdout)

    def test_plan_rejected_with_real_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "plan.md"
            write_lines(p, BUDGETS["plan"] + 41)
            result = run(["plan", str(p)])
            self.assertEqual(result.returncode, 1)
            self.assertIn("presupuesto-plan-excedido", result.stdout)
            self.assertIn(str(BUDGETS["plan"] + 41), result.stdout)
            self.assertIn(str(BUDGETS["plan"]), result.stdout)

    def test_bitacora_with_several_short_entries_within_budget_passes(self) -> None:
        """Distinto de `BitacoraBudgetTests` (una sola entrada al borde del
        presupuesto): varias entradas cortas, ninguna excede — forma
        realista de una bitácora compacta (unidad 0151, formato de 1 línea
        por evento)."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bitacora.md"
            entries = "".join(
                f"## 2026-09-{20 + i:02d}T00:00Z · entrada {i}\nlínea {i}\n"
                for i in range(1, 6)
            )
            p.write_text(entries, encoding="utf-8")
            result = run(["bitacora", str(p)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class BudgetCoverageTests(unittest.TestCase):
    """CA-06 — `budget-coverage --root <dir> --gate aprobado`."""

    def _make_unit(self, root: Path, name: str, *, verdict: str,
                    spec_lines: int, plan_lines: int) -> None:
        unit_dir = root / name
        unit_dir.mkdir(parents=True)
        (unit_dir / "_estado.yaml").write_text(
            "id: " + name + "\n"
            "gates:\n"
            "  spec:\n"
            "    veredicto: refinado\n"
            "  codigo:\n"
            f"    veredicto: {verdict}\n",
            encoding="utf-8",
        )
        write_lines(unit_dir / "spec.md", spec_lines)
        write_lines(unit_dir / "plan.md", plan_lines)

    def test_coverage_below_threshold_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 1 of 2 approved units exceeds its spec.md budget -> 50% < 90%.
            self._make_unit(root, "0001-uno", verdict="aprobado",
                             spec_lines=100, plan_lines=100)
            self._make_unit(root, "0002-dos", verdict="aprobado",
                             spec_lines=BUDGETS["spec"] + 1, plan_lines=100)
            result = run(["budget-coverage", "--root", str(root), "--gate", "aprobado"])
            self.assertEqual(result.returncode, 1)
            self.assertIn("cobertura-presupuesto-insuficiente", result.stdout)
            self.assertIn("0002-dos", result.stdout)

    def test_coverage_at_or_above_threshold_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(9):
                self._make_unit(root, f"000{i}-uno", verdict="aprobado",
                                 spec_lines=100, plan_lines=100)
            self._make_unit(root, "0010-diez", verdict="aprobado",
                             spec_lines=BUDGETS["spec"] + 1, plan_lines=100)
            result = run(["budget-coverage", "--root", str(root), "--gate", "aprobado"])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)

    def test_refinado_verdict_is_counted_as_aprobado(self) -> None:
        """0116-D2: `--gate aprobado` incluye también `refinado` (0117-D6:
        `refinado` es el veredicto normal de un gate que pasó tras
        correcciones del panel, no un rechazo) — si no se contara, su
        spec.md sobredimensionado no aparecería."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_unit(root, "0001-incluida", verdict="refinado",
                             spec_lines=BUDGETS["spec"] + 1, plan_lines=100)
            result = run(["budget-coverage", "--root", str(root), "--gate", "aprobado"])
            self.assertEqual(result.returncode, 1)
            self.assertIn("0001-incluida", result.stdout)

    def test_only_units_matching_gate_verdict_are_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # `escalado` -> excluded (never counted as "aprobado"); if it
            # counted, its oversized spec.md would fail the run.
            self._make_unit(root, "0001-excluida", verdict="escalado",
                             spec_lines=BUDGETS["spec"] + 1, plan_lines=100)
            result = run(["budget-coverage", "--root", str(root), "--gate", "aprobado"])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("0001-excluida", result.stdout)

    def test_no_matching_units_is_a_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run(["budget-coverage", "--root", str(root), "--gate", "aprobado"])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
