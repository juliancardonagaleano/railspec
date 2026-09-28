"""Tests for `.spec/scripts/sdd_retomar.py` (CA-04).

Covers the single behavior this unit adds: a unit whose `_estado.yaml` has
no `modo:` field at all resumes read as `modo: interactivo` — the new
default, replacing the retired one.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS_DIR / "sdd_retomar.py"


def _run(hint: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), hint],
        capture_output=True, text=True, check=False,
    )


class SddRetomarDefaultModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.unit_dir = Path(self._tmp.name) / "0999-unidad-sin-modo"
        self.unit_dir.mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def _write_state(self, body: str) -> None:
        (self.unit_dir / "_estado.yaml").write_text(body, encoding="utf-8")

    def test_unit_without_modo_field_resumes_as_interactivo(self) -> None:
        self._write_state("fase: research\nestado: en-curso\n")
        result = _run(str(self.unit_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["kind"], "resume")
        self.assertEqual(payload["modo"], "interactivo")

    def test_unit_with_explicit_modo_keeps_its_declared_value(self) -> None:
        self._write_state("fase: research\nestado: en-curso\nmodo: supervisado\n")
        result = _run(str(self.unit_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["modo"], "supervisado")


if __name__ == "__main__":
    unittest.main()
