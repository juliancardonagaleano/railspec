"""Tests para `.spec/scripts/generate_minor_changes_index.py`."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import generate_minor_changes_index as gmci  # noqa: E402

ENTRY_A = (
    "## 2026-09-20 — primera-entrada\n"
    "- qué: primer cambio de prueba\n"
    "- por qué: cubrir el generador\n"
    "- evidencia: abc123\n"
    "- gate: diff 3 líneas, 0 archivos nuevos\n"
)
ENTRY_B = (
    "## 2026-09-24 — segunda-entrada\n"
    "- qué: segundo cambio de prueba\n"
    "- por qué: cubrir el orden por fecha\n"
    "- evidencia: def456\n"
    "- gate: diff 5 líneas, 1 archivo nuevo\n"
)


class GenerateReadme(unittest.TestCase):
    def _make_entries_dir(self, tmp_dir: str) -> Path:
        entries_dir = Path(tmp_dir) / "cambios-menores"
        entries_dir.mkdir()
        (entries_dir / "2026-09-20-primera-entrada.md").write_text(ENTRY_A, encoding="utf-8")
        (entries_dir / "2026-09-24-segunda-entrada.md").write_text(ENTRY_B, encoding="utf-8")
        return entries_dir

    def test_generate_creates_readme_with_two_entries_desc_by_date(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            entries_dir = self._make_entries_dir(tmp_dir)
            readme_path = entries_dir / "README.md"
            rc = gmci.main(["--entries-dir", str(entries_dir), "--readme", str(readme_path)])
            self.assertEqual(rc, 0)
            content = readme_path.read_text(encoding="utf-8")
            pos_b = content.index("segunda-entrada")
            pos_a = content.index("primera-entrada")
            self.assertLess(pos_b, pos_a)
            self.assertIn("segundo cambio de prueba", content)
            self.assertIn("primer cambio de prueba", content)

    def test_two_runs_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            entries_dir = self._make_entries_dir(tmp_dir)
            readme_path = entries_dir / "README.md"
            gmci.main(["--entries-dir", str(entries_dir), "--readme", str(readme_path)])
            first = readme_path.read_bytes()
            gmci.main(["--entries-dir", str(entries_dir), "--readme", str(readme_path)])
            second = readme_path.read_bytes()
            self.assertEqual(first, second)

    def test_check_exits_0_after_generate(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            entries_dir = self._make_entries_dir(tmp_dir)
            readme_path = entries_dir / "README.md"
            gmci.main(["--entries-dir", str(entries_dir), "--readme", str(readme_path)])
            rc = gmci.main(["--check", "--entries-dir", str(entries_dir), "--readme", str(readme_path)])
            self.assertEqual(rc, 0)

    def test_check_exits_1_when_readme_altered(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            entries_dir = self._make_entries_dir(tmp_dir)
            readme_path = entries_dir / "README.md"
            gmci.main(["--entries-dir", str(entries_dir), "--readme", str(readme_path)])
            readme_path.write_text("contenido alterado a mano\n", encoding="utf-8")
            rc = gmci.main(["--check", "--entries-dir", str(entries_dir), "--readme", str(readme_path)])
            self.assertEqual(rc, 1)

    def test_check_exits_1_when_readme_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            entries_dir = self._make_entries_dir(tmp_dir)
            readme_path = entries_dir / "README.md"
            rc = gmci.main(["--check", "--entries-dir", str(entries_dir), "--readme", str(readme_path)])
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
