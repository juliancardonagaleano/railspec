"""CA-04 — El outlier ``install_pre_push_hook.sh`` aparece tanto en la salida
del verificador como en la tabla de excepciones de `.spec/README.md §
nomenclatura`.

Esta es la verificación del outlier ya conocido y load-bearing documentado en
el spec (DD-5). El verificador **no** lo oculta de la salida — las
excepciones vigentes son outliers humanos-aceptados, no reglas permisivas.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import naming_check  # noqa: E402

REPO_ROOT = TESTS.parents[2]
README_PATH = REPO_ROOT / ".spec" / "README.md"
TARGET = "install_pre_push_hook.sh"


class InstallPrePushHookListedTests(unittest.TestCase):

    def test_u0005_ca04_naming_section_has_target(self) -> None:
        text = README_PATH.read_text(encoding="utf-8")
        self.assertIn(TARGET, text,
                      "el README § nomenclatura debe mencionar "
                      "install_pre_push_hook.sh")

    def test_u0005_ca04_exception_table_has_target(self) -> None:
        text = README_PATH.read_text(encoding="utf-8")
        nom = naming_check.parse_nomenclature(text)
        exceptions: set[str] = nom["exceptions"]  # type: ignore[assignment]
        self.assertIn(
            "install_pre_push_hook", exceptions,
            "el stem 'install_pre_push_hook' debe estar en la tabla de "
            "excepciones vigentes del README § nomenclatura",
        )

    def test_u0005_ca04_load_bearing_motive_documented(self) -> None:
        """El motivo load-bearing para installer/installer.py:81 debe estar."""
        text = README_PATH.read_text(encoding="utf-8")
        start = text.find("# nomenclatura:")
        # La sección `# nomenclatura:` termina en el próximo `#` (no `##`),
        # porque `# nomenclatura:` es de nivel 1 (un solo `#`).
        end = text.find("\n# ", start + 1)
        if end == -1:
            end = len(text)
        section = text[start:end]
        self.assertIn("installer/installer.py:81", section,
                      "el motivo load-bearing (installer/installer.py:81) "
                      "debe documentarse en la sección de nomenclatura")

    def test_u0005_ca04_verifier_lists_target_in_stdout(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = naming_check.run(REPO_ROOT)
        self.assertEqual(rc, 0)
        self.assertIn(TARGET, buf.getvalue(),
                      "el verificador debe nombrar install_pre_push_hook.sh "
                      "en stdout como outlier load-bearing")

    def test_u0005_ca04_verifier_lists_target_in_find_outliers(self) -> None:
        outliers = naming_check.find_outliers(REPO_ROOT)
        rels = [rel for rel, name, _ in outliers if name == TARGET]
        self.assertTrue(
            rels,
            "find_outliers debe retornar install_pre_push_hook.sh",
        )
        self.assertTrue(
            rels[0].startswith("scripts/"),
            f"el path debe estar bajo scripts/, recibí: {rels[0]}",
        )


if __name__ == "__main__":
    unittest.main()