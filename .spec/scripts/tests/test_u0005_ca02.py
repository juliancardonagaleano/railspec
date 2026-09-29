"""CA-02 — Verificador de nomenclatura.

El verificador:

1. Lee la convención declarada en `.spec/README.md` (anchor `# nomenclatura:`).
   **No** la hardcodea — la tabla del README es la única fuente.
2. Recorre ``scripts/``, ``installer/``, ``.spec/scripts/``, ``.agents/``,
   ``.claude/``, ``.spec/units/``.
3. Lista los outliers por ``ruta:nombre`` con ``exit 0`` y stdout no vacío.

El módulo compartido ``naming_check`` encapsula el parser de la sección y el
recorrido; los tests de CA-03 y CA-04 lo invocan desde fixtures y comprueban
la salida, respectivamente.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import naming_check  # noqa: E402

REPO_ROOT = TESTS.parents[2]
README_PATH = REPO_ROOT / ".spec" / "README.md"


class ReadsConventionFromReadmeTests(unittest.TestCase):
    """CA-02 paso 1 — la convención se lee del README."""

    def test_u0005_ca02_readme_has_exactly_one_nomenclature_anchor(self) -> None:
        text = README_PATH.read_text(encoding="utf-8")
        matches = [ln for ln in text.splitlines() if ln.startswith("# nomenclatura")]
        self.assertEqual(
            len(matches), 1,
            f"se esperaba 1 anchor '# nomenclatura:' en .spec/README.md, "
            f"encontré {len(matches)}: {matches}",
        )
        self.assertTrue(naming_check.expected_nomenclature_section_present(text))

    def test_u0005_ca02_parses_ext_rules_from_readme(self) -> None:
        text = README_PATH.read_text(encoding="utf-8")
        nom = naming_check.parse_nomenclature(text)
        exts: dict[str, str] = nom["extensions"]  # type: ignore[assignment]
        self.assertEqual(exts[".py"], "snake_case")
        self.assertEqual(exts[".sh"], "kebab_case")
        self.assertEqual(exts[".md"], "kebab_or_single_word")
        self.assertEqual(exts[".yaml"], "snake_case")

    def test_u0005_ca02_parses_install_pre_push_hook_exception(self) -> None:
        text = README_PATH.read_text(encoding="utf-8")
        nom = naming_check.parse_nomenclature(text)
        exceptions: set[str] = nom["exceptions"]  # type: ignore[assignment]
        self.assertIn(
            "install_pre_push_hook", exceptions,
            "el README § nomenclatura debe listar install_pre_push_hook.sh "
            "como excepción vigente (load-bearing para installer/installer.py:81)",
        )
        self.assertIn("mcp-pce", exceptions)
        self.assertIn("MODELO-AGENTES", exceptions)


class ListsOutliersTests(unittest.TestCase):
    """CA-02 paso 2-3 — recorre los scopes y emite outliers con exit 0."""

    def test_u0005_ca02_walks_all_required_scopes(self) -> None:
        for scope in naming_check.DEFAULT_SCOPES:
            with self.subTest(scope=scope):
                self.assertTrue(
                    (REPO_ROOT / scope).is_dir(),
                    f"scope obligatorio {scope} no existe bajo la raíz",
                )

    def test_u0005_ca02_lists_outliers_for_real_repo(self) -> None:
        outliers = naming_check.find_outliers(REPO_ROOT)
        self.assertGreater(
            len(outliers), 0,
            "el verificador debe reportar al menos un outlier del repo real "
            "(install_pre_push_hook.sh, mcp-pce.py y otros)",
        )
        for rel, name, rule in outliers:
            with self.subTest(name=name):
                self.assertTrue(rel.startswith("scripts/")
                                or rel.startswith("installer/")
                                or rel.startswith(".spec/scripts/")
                                or rel.startswith(".agents/")
                                or rel.startswith(".claude/")
                                or rel.startswith(".spec/units/"),
                                f"outlier fuera de los scopes: {rel}")
                self.assertIsInstance(name, str) and name
                self.assertIsInstance(rule, str) and rule

    def test_u0005_ca02_outlier_path_format(self) -> None:
        outliers = naming_check.find_outliers(REPO_ROOT)
        for rel, name, _rule in outliers:
            with self.subTest(rel=rel):
                self.assertIn(name, rel)


class VerifierCliTests(unittest.TestCase):
    """El wrapper CLI imprime outliers uno por línea y retorna 0."""

    def test_u0005_ca02_run_function_returns_zero(self) -> None:
        rc = naming_check.run(REPO_ROOT)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()