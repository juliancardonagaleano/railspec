"""Tests for `test_subset.py` (unit 0121, G4 — CA-11/CA-12).

Every case calls the pure mapping functions directly with injected file
lists (no `git`, plan.md § Decisiones de diseño); the stem-lookup for
`orchestrator/` needs a real (temporary) `orchestrator/tests/` tree since
that lookup is a filesystem glob, not a git call.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import test_subset  # noqa: E402


def _make_repo(tmp: Path, test_files: list[str]) -> Path:
    """A throwaway repo root with `orchestrator/tests/<test_files>` created
    (empty content — only the stem-lookup glob cares that they exist)."""
    for rel in test_files:
        p = tmp / "orchestrator" / "tests" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    return tmp


class OrchestratorMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test-subset-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.root = Path(self._tmp)

    # --- 1:1 clean mapping ------------------------------------------------

    def test_archivo_de_test_tocado_se_agrega_tal_cual(self) -> None:
        _make_repo(self.root, ["unit/test_foo.py"])
        subset, reason = test_subset.map_orchestrator(
            ["orchestrator/tests/unit/test_foo.py"], self.root)
        self.assertEqual(subset, ["orchestrator/tests/unit/test_foo.py"])
        self.assertEqual(reason, "ok")

    def test_archivo_de_src_con_un_solo_test_por_convencion_se_mapea(self) -> None:
        _make_repo(self.root, ["unit/test_foo.py"])
        subset, reason = test_subset.map_orchestrator(
            ["orchestrator/src/iark_orchestrator/pipeline/foo.py"], self.root)
        self.assertEqual(subset, ["orchestrator/tests/unit/test_foo.py"])
        self.assertEqual(reason, "ok")

    def test_combina_test_directo_y_src_mapeado_sin_duplicar(self) -> None:
        _make_repo(self.root, ["unit/test_foo.py", "unit/test_bar.py"])
        subset, reason = test_subset.map_orchestrator(
            [
                "orchestrator/tests/unit/test_bar.py",
                "orchestrator/src/iark_orchestrator/pipeline/foo.py",
            ], self.root)
        self.assertEqual(
            subset,
            ["orchestrator/tests/unit/test_bar.py", "orchestrator/tests/unit/test_foo.py"])
        self.assertEqual(reason, "ok")

    # --- fallback: cero o más de un match ----------------------------------

    def test_src_sin_ningun_test_por_convencion_descarta_el_subset(self) -> None:
        _make_repo(self.root, [])
        subset, reason = test_subset.map_orchestrator(
            ["orchestrator/src/iark_orchestrator/pipeline/foo.py"], self.root)
        self.assertIsNone(subset)
        self.assertIn("0 test", reason)

    def test_src_con_dos_matches_de_stem_descarta_el_subset(self) -> None:
        _make_repo(self.root, ["unit/test_foo.py", "integration/test_foo.py"])
        subset, reason = test_subset.map_orchestrator(
            ["orchestrator/src/iark_orchestrator/pipeline/foo.py"], self.root)
        self.assertIsNone(subset)
        self.assertIn("2 test", reason)

    # --- fallback: archivo no mapeable en ninguna convención ---------------

    def test_archivo_de_configuracion_descarta_todo_el_subset_de_orchestrator(self) -> None:
        _make_repo(self.root, ["unit/test_foo.py"])
        subset, reason = test_subset.map_orchestrator(
            [
                "orchestrator/tests/unit/test_foo.py",
                "orchestrator/pyproject.toml",
            ], self.root)
        self.assertIsNone(subset)
        self.assertIn("pyproject.toml", reason)

    def test_conftest_no_es_mapeable_y_descarta_el_subset(self) -> None:
        _make_repo(self.root, [])
        subset, reason = test_subset.map_orchestrator(
            ["orchestrator/tests/conftest.py"], self.root)
        self.assertIsNone(subset)
        self.assertNotEqual(reason, "no-aplica")

    # --- no-aplica: nada tocado en orchestrator/ ---------------------------

    def test_sin_archivos_de_orchestrator_es_no_aplica(self) -> None:
        subset, reason = test_subset.map_orchestrator(
            ["studio/app/src/foo.ts"], self.root)
        self.assertIsNone(subset)
        self.assertEqual(reason, "no-aplica")


class StudioAppMappingTests(unittest.TestCase):
    def test_archivo_de_src_se_agrega(self) -> None:
        subset, reason = test_subset.map_studio_app(["studio/app/src/foo.ts"])
        self.assertEqual(subset, ["studio/app/src/foo.ts"])
        self.assertEqual(reason, "ok")

    def test_archivo_spec_ts_se_agrega(self) -> None:
        subset, reason = test_subset.map_studio_app(
            ["studio/app/tests/foo.spec.ts"])
        self.assertEqual(subset, ["studio/app/tests/foo.spec.ts"])
        self.assertEqual(reason, "ok")

    def test_archivo_test_ts_se_agrega(self) -> None:
        subset, reason = test_subset.map_studio_app(
            ["studio/app/tests/foo.test.ts"])
        self.assertEqual(subset, ["studio/app/tests/foo.test.ts"])
        self.assertEqual(reason, "ok")

    def test_jest_config_descarta_el_subset(self) -> None:
        subset, reason = test_subset.map_studio_app(
            ["studio/app/src/foo.ts", "studio/app/jest.config.ts"])
        self.assertIsNone(subset)
        self.assertIn("jest.config.ts", reason)

    def test_package_json_descarta_el_subset(self) -> None:
        subset, reason = test_subset.map_studio_app(["studio/app/package.json"])
        self.assertIsNone(subset)
        self.assertNotEqual(reason, "no-aplica")

    def test_sin_archivos_de_studio_app_es_no_aplica(self) -> None:
        subset, reason = test_subset.map_studio_app(
            ["orchestrator/tests/unit/test_foo.py"])
        self.assertIsNone(subset)
        self.assertEqual(reason, "no-aplica")


class PlanCombinedTests(unittest.TestCase):
    """CA-12: ambos subárboles tocados a la vez, y ninguno tocado."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test-subset-plan-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.root = Path(self._tmp)

    def test_ambos_subarboles_tocados_emiten_ambos_comandos(self) -> None:
        _make_repo(self.root, ["unit/test_foo.py"])
        results = test_subset.plan(
            [
                "orchestrator/tests/unit/test_foo.py",
                "studio/app/src/foo.ts",
            ], self.root)
        by_subtree = {r.subtree: r for r in results}
        self.assertEqual(by_subtree["orchestrator"].status, "subset")
        self.assertIn("tests/unit/test_foo.py", by_subtree["orchestrator"].command)
        self.assertEqual(by_subtree["studio/app"].status, "subset")
        self.assertIn("studio/app/src/foo.ts", by_subtree["studio/app"].command)

    def test_ningun_archivo_tocado_en_ninguno_de_los_dos_es_no_aplica_en_ambos(
        self,
    ) -> None:
        # `studio/package.json` es del backend `studio/`, no de `studio/app/` —
        # ninguno de los dos subárboles que cubre este script lo ve tocado.
        results = test_subset.plan(["studio/package.json"], self.root)
        by_subtree = {r.subtree: r for r in results}
        self.assertEqual(by_subtree["orchestrator"].status, "no-aplica")
        self.assertEqual(by_subtree["studio/app"].status, "no-aplica")

    def test_no_tocar_ninguno_no_es_incumplimiento_no_lanza_excepcion(self) -> None:
        try:
            results = test_subset.plan([], self.root)
        except Exception as exc:  # pragma: no cover - documents the guarantee
            self.fail(f"plan([]) no debe fallar nunca: {exc}")
        self.assertTrue(all(r.status == "no-aplica" for r in results))


class CommandBuildingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test-subset-cmd-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.root = Path(self._tmp)

    def test_comando_de_fallback_de_orchestrator_es_la_corrida_completa(self) -> None:
        result = test_subset.orchestrator_result(["orchestrator/pyproject.toml"], self.root)
        self.assertEqual(result.status, "fallback")
        self.assertEqual(result.command, "cd orchestrator && uv run pytest")

    def test_comando_de_fallback_de_studio_app_es_npm_test(self) -> None:
        result = test_subset.studio_app_result(["studio/app/package.json"])
        self.assertEqual(result.status, "fallback")
        self.assertEqual(result.command, "cd studio/app && npm test")

    def test_comando_de_subset_de_studio_app_usa_find_related_tests(self) -> None:
        result = test_subset.studio_app_result(["studio/app/src/foo.ts"])
        self.assertIn("--findRelatedTests", result.command)
        self.assertIn("studio/app/src/foo.ts", result.command)

    def test_comando_de_subset_de_orchestrator_usa_rutas_relativas_al_subarbol(
        self,
    ) -> None:
        _make_repo(self.root, ["unit/test_foo.py"])
        result = test_subset.orchestrator_result(
            ["orchestrator/tests/unit/test_foo.py"], self.root)
        self.assertEqual(result.command, "cd orchestrator && uv run pytest -m \"\" tests/unit/test_foo.py")

    def test_comando_de_subset_de_orchestrator_anula_el_filtro_de_integration_de_addopts(
        self,
    ) -> None:
        """Regresión (gate de código de la unidad 0121, hallazgo L2): sin el
        `-m ""`, un archivo tocado marcado `integration` produciría un subset
        que deselecciona el 100% de sus propios tests ("no tests ran"),
        violando el invariante de que el subset es superconjunto o igual del
        test del archivo tocado. `-m` es "último valor gana" en pytest, así
        que este `-m ""` neutraliza el `-m "not integration"` que `addopts`
        inyecta por defecto (unidad 0121, T3)."""
        _make_repo(self.root, ["test_mongo_integration.py"])
        result = test_subset.orchestrator_result(
            ["orchestrator/tests/test_mongo_integration.py"], self.root)
        self.assertIn('-m ""', result.command)


if __name__ == "__main__":
    unittest.main()
