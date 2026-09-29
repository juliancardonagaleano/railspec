"""CA-03 — El verificador lista outliers en stdout con exit 0.

Ejecuta el verificador de CA-02 contra un fixture temporal con un outlier
conocido y verifica que su nombre aparece literalmente en stdout con
``exit 0``.
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


class FixtureOutlierTests(unittest.TestCase):

    def _populate_fixture(self, root: Path) -> None:
        """Crea un árbol mínimo con scope `scripts/` y un outlier conocido.

        ``kebab-case-in-py.py`` viola la regla ``.py → snake_case`` (es kebab
        en una carpeta ``.py``). El segundo es snake en una carpeta ``.sh``
        (debería ser kebab).
        """
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "ok_name.py").write_text("# ok\n")
        (scripts / "kebab-case-in-py.py").write_text(
            "# outlier deliberado — kebab en carpeta .py\n"
        )
        (scripts / "snake_in_sh.sh").write_text(
            "# outlier deliberado — snake en carpeta .sh\n"
        )

    def test_u0005_ca03_lists_outlier_in_stdout_with_exit_zero(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._populate_fixture(root)

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = naming_check.run(repo_root=root, scopes=["scripts"])

            self.assertEqual(rc, 0, "el verificador debe retornar 0 con outliers")
            output = buf.getvalue()
            self.assertIn("kebab-case-in-py.py", output,
                          "el nombre del outlier kebab-en-py debe aparecer en stdout")
            self.assertIn("snake_in_sh.sh", output,
                          "el nombre del outlier snake-en-sh debe aparecer en stdout")

    def test_u0005_ca03_stdout_non_empty_when_outlier_present(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._populate_fixture(root)

            buf = io.StringIO()
            with redirect_stdout(buf):
                naming_check.run(repo_root=root, scopes=["scripts"])

            self.assertTrue(
                buf.getvalue().strip(),
                "stdout no debe estar vacío cuando hay outliers (CA-03)",
            )

    def test_u0005_ca03_compliant_files_not_in_outliers(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._populate_fixture(root)

            outliers = naming_check.find_outliers(root, scopes=["scripts"])
            names = {name for _rel, name, _rule in outliers}
            self.assertIn("kebab-case-in-py.py", names)
            self.assertNotIn(
                "ok_name.py", names,
                "un nombre que cumple la convención no debe aparecer como outlier",
            )


if __name__ == "__main__":
    unittest.main()