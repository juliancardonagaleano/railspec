"""Tests deterministas para `.spec/scripts/classify_change.py`."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from classify_change import classify, parse_entry_file, validate_entry  # noqa: E402


class TrivialBranch(unittest.TestCase):
    """trivial: new_files==0 y lines_net<=5 y no contract_change."""

    def test_zero_files_zero_lines(self):
        self.assertEqual(classify(0, 0, False), "trivial")

    def test_zero_files_five_lines(self):
        self.assertEqual(classify(0, 5, False), "trivial")

    def test_zero_files_one_line(self):
        self.assertEqual(classify(0, 1, False), "trivial")

    def test_renombre_within_5_lines(self):
        self.assertEqual(classify(0, 3, False), "trivial")

    def test_typo_fix(self):
        self.assertEqual(classify(0, 1, False), "trivial")


class MenorBranch(unittest.TestCase):
    """menor: 0<new_files<=1 y lines_net<=50 y no contract_change."""

    def test_one_file_zero_lines(self):
        self.assertEqual(classify(1, 0, False), "menor")

    def test_one_file_50_lines(self):
        self.assertEqual(classify(1, 50, False), "menor")

    def test_one_file_27_lines(self):
        self.assertEqual(classify(1, 27, False), "menor")

    def test_zero_files_50_lines(self):
        self.assertEqual(classify(0, 50, False), "menor")

    def test_zero_files_30_lines(self):
        self.assertEqual(classify(0, 30, False), "menor")


class NoTrivialBranch(unittest.TestCase):
    """no_trivial: excede los límites."""

    def test_two_files(self):
        self.assertEqual(classify(2, 0, False), "no_trivial")

    def test_one_file_51_lines(self):
        self.assertEqual(classify(1, 51, False), "no_trivial")

    def test_contract_change(self):
        self.assertEqual(classify(0, 0, True), "no_trivial")

    def test_one_file_zero_lines_with_contract(self):
        self.assertEqual(classify(1, 0, True), "no_trivial")

    def test_zero_files_100_lines(self):
        self.assertEqual(classify(0, 100, False), "no_trivial")


class ValidateEntry(unittest.TestCase):
    """Mini-gate determinista (T4): rechaza entradas inválidas."""

    def _ok(self):
        return {
            "id": "fast-001",
            "que": "fix typo",
            "por_que": "CI rojo",
            "evidencia": "abc123",
            "gate": "diff 3 líneas, 0 archivos nuevos",
        }

    def test_valid_entry_passes(self):
        validate_entry(self._ok())

    def test_missing_field_raises(self):
        e = self._ok()
        e["que"] = ""
        with self.assertRaises(ValueError):
            validate_entry(e)

    def test_lines_net_over_budget_raises(self):
        e = self._ok()
        e["lines_net"] = "51"
        with self.assertRaises(ValueError):
            validate_entry(e)

    def test_lines_net_at_budget_passes(self):
        e = self._ok()
        e["lines_net"] = "50"
        validate_entry(e)


class ValidateEntryContractChange(unittest.TestCase):
    """`validate_entry` no rechaza `contract_change = true` — ese campo es una
    declaración humana ya tomada en el triaje, no algo que este mini-gate
    determinista re-evalúe (documentado, sin cambio de comportamiento)."""

    def test_contract_change_true_still_passes(self):
        entry = {
            "id": "fast-002",
            "que": "cambio con contrato modificado",
            "por_que": "decisión humana registrada en el triaje",
            "evidencia": "abc123",
            "gate": "diff 10 líneas, 1 archivo nuevo",
            "contract_change": True,
        }
        validate_entry(entry)


class ParseEntryFile(unittest.TestCase):
    """`parse_entry_file` — plantilla de 5 líneas hacia el dict de `validate_entry`."""

    def _write(self, tmp_dir: str, body: str) -> Path:
        entry_path = Path(tmp_dir) / "2026-09-24-caso-de-prueba.md"
        entry_path.write_text(body, encoding="utf-8")
        return entry_path

    def test_valid_entry_parses_and_validates(self):
        body = (
            "## 2026-09-24 — caso-de-prueba\n"
            "- qué: cambio de prueba\n"
            "- por qué: cubrir el parser\n"
            "- evidencia: git diff --stat → 1 archivo, 8 líneas netas\n"
            "- gate: mini-gate PASS, 8 líneas netas (umbral ≤ 50)\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            entry_path = self._write(tmp_dir, body)
            entry = parse_entry_file(entry_path)
            self.assertEqual(entry["id"], "caso-de-prueba")
            self.assertEqual(entry["lines_net"], "8")
            validate_entry(entry)

    def test_missing_field_fails_validation(self):
        body = (
            "## 2026-09-24 — caso-de-prueba\n"
            "- qué: cambio de prueba\n"
            "- por qué: \n"
            "- evidencia: git diff --stat → 1 archivo\n"
            "- gate: mini-gate PASS\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            entry_path = self._write(tmp_dir, body)
            entry = parse_entry_file(entry_path)
            with self.assertRaises(ValueError):
                validate_entry(entry)

    def test_lines_net_over_budget_omitted_when_gate_states_excede(self):
        body = (
            "## 2026-09-24 — caso-de-prueba\n"
            "- qué: cambio grande con override humano\n"
            "- por qué: cubrir la excepción documentada\n"
            "- evidencia: git diff --stat → 1 archivo, 155 líneas\n"
            "- gate: 155 líneas netas; **excede umbral fast-path puro** por decisión humana\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            entry_path = self._write(tmp_dir, body)
            entry = parse_entry_file(entry_path)
            self.assertNotIn("lines_net", entry)
            validate_entry(entry)

    def test_pure_removal_keeps_its_negative_sign(self):
        """Un retiro puro se declara con signo y pasa: sin él, el valor
        absoluto lo hacía "exceder el presupuesto"."""
        for sign in ("-", "−"):
            with self.subTest(sign=sign):
                body = (
                    "## 2026-09-24 — caso-de-prueba\n"
                    "- qué: retiro de un control obsoleto\n"
                    "- por qué: cubrir el signo en la extracción\n"
                    "- evidencia: git diff --stat → 2 archivos\n"
                    f"- gate: mini-gate PASS, {sign}299 líneas netas\n"
                )
                with tempfile.TemporaryDirectory() as tmp_dir:
                    entry_path = self._write(tmp_dir, body)
                    entry = parse_entry_file(entry_path)
                    self.assertEqual(entry["lines_net"], "-299")
                    validate_entry(entry)


if __name__ == "__main__":
    unittest.main()
