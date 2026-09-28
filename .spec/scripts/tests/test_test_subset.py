"""Tests for `test_subset.py` — motor genérico del kit.

Every case calls the pure mapping functions directly with injected file
lists (no `git`); the stem-lookup for un subárbol `pytest` needs a real
(temporary) `<ruta>tests/` tree since that lookup is a filesystem glob, not
a git call. Los dos subárboles de ejemplo (`orchestrator/`, `studio/app/`)
son nombres de fixture arbitrarios de este test — el motor no los cablea en
su código (CA-09): cualquier `ruta`/`runner` viene de `subarboles_test` de
`.spec/protocolo-datos.yaml`.
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

ORCH = test_subset.Subarbol(
    ruta="orchestrator/", runner="pytest",
    comando_completo="cd orchestrator && uv run pytest")
STUDIO = test_subset.Subarbol(
    ruta="studio/app/", runner="jest",
    comando_completo="cd studio/app && npm test")


def _make_repo(tmp: Path, test_files: list[str]) -> Path:
    """A throwaway repo root with `orchestrator/tests/<test_files>` created
    (empty content — only the stem-lookup glob cares that they exist)."""
    for rel in test_files:
        p = tmp / "orchestrator" / "tests" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    return tmp


class PytestSubtreeMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test-subset-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.root = Path(self._tmp)

    # --- 1:1 clean mapping ------------------------------------------------

    def test_archivo_de_test_tocado_se_agrega_tal_cual(self) -> None:
        _make_repo(self.root, ["unit/test_foo.py"])
        subset, reason = test_subset.map_pytest_subtree(
            ["orchestrator/tests/unit/test_foo.py"], self.root, "orchestrator/")
        self.assertEqual(subset, ["orchestrator/tests/unit/test_foo.py"])
        self.assertEqual(reason, "ok")

    def test_archivo_fuera_de_tests_con_un_solo_test_por_convencion_se_mapea(self) -> None:
        _make_repo(self.root, ["unit/test_foo.py"])
        subset, reason = test_subset.map_pytest_subtree(
            ["orchestrator/src/pipeline/foo.py"], self.root, "orchestrator/")
        self.assertEqual(subset, ["orchestrator/tests/unit/test_foo.py"])
        self.assertEqual(reason, "ok")

    def test_combina_test_directo_y_fuente_mapeada_sin_duplicar(self) -> None:
        _make_repo(self.root, ["unit/test_foo.py", "unit/test_bar.py"])
        subset, reason = test_subset.map_pytest_subtree(
            [
                "orchestrator/tests/unit/test_bar.py",
                "orchestrator/src/pipeline/foo.py",
            ], self.root, "orchestrator/")
        self.assertEqual(
            subset,
            ["orchestrator/tests/unit/test_bar.py", "orchestrator/tests/unit/test_foo.py"])
        self.assertEqual(reason, "ok")

    # --- fallback: cero o más de un match ----------------------------------

    def test_fuente_sin_ningun_test_por_convencion_descarta_el_subset(self) -> None:
        _make_repo(self.root, [])
        subset, reason = test_subset.map_pytest_subtree(
            ["orchestrator/src/pipeline/foo.py"], self.root, "orchestrator/")
        self.assertIsNone(subset)
        self.assertIn("0 test", reason)

    def test_fuente_con_dos_matches_de_stem_descarta_el_subset(self) -> None:
        _make_repo(self.root, ["unit/test_foo.py", "integration/test_foo.py"])
        subset, reason = test_subset.map_pytest_subtree(
            ["orchestrator/src/pipeline/foo.py"], self.root, "orchestrator/")
        self.assertIsNone(subset)
        self.assertIn("2 test", reason)

    # --- fallback: archivo no mapeable en ninguna convención ---------------

    def test_archivo_de_configuracion_descarta_todo_el_subset_del_subarbol(self) -> None:
        _make_repo(self.root, ["unit/test_foo.py"])
        subset, reason = test_subset.map_pytest_subtree(
            [
                "orchestrator/tests/unit/test_foo.py",
                "orchestrator/pyproject.toml",
            ], self.root, "orchestrator/")
        self.assertIsNone(subset)
        self.assertIn("pyproject.toml", reason)

    def test_archivo_de_test_sin_prefijo_test_no_es_mapeable_y_descarta_el_subset(self) -> None:
        _make_repo(self.root, [])
        subset, reason = test_subset.map_pytest_subtree(
            ["orchestrator/tests/conftest.py"], self.root, "orchestrator/")
        self.assertIsNone(subset)
        self.assertNotEqual(reason, "no-aplica")

    # --- no-aplica: nada tocado en el subárbol -----------------------------

    def test_sin_archivos_del_subarbol_es_no_aplica(self) -> None:
        subset, reason = test_subset.map_pytest_subtree(
            ["studio/app/src/foo.ts"], self.root, "orchestrator/")
        self.assertIsNone(subset)
        self.assertEqual(reason, "no-aplica")


class JestSubtreeMappingTests(unittest.TestCase):
    def test_archivo_de_src_se_agrega(self) -> None:
        subset, reason = test_subset.map_jest_subtree(
            ["studio/app/src/foo.ts"], "studio/app/")
        self.assertEqual(subset, ["studio/app/src/foo.ts"])
        self.assertEqual(reason, "ok")

    def test_archivo_spec_ts_se_agrega(self) -> None:
        subset, reason = test_subset.map_jest_subtree(
            ["studio/app/tests/foo.spec.ts"], "studio/app/")
        self.assertEqual(subset, ["studio/app/tests/foo.spec.ts"])
        self.assertEqual(reason, "ok")

    def test_archivo_test_ts_se_agrega(self) -> None:
        subset, reason = test_subset.map_jest_subtree(
            ["studio/app/tests/foo.test.ts"], "studio/app/")
        self.assertEqual(subset, ["studio/app/tests/foo.test.ts"])
        self.assertEqual(reason, "ok")

    def test_jest_config_descarta_el_subset(self) -> None:
        subset, reason = test_subset.map_jest_subtree(
            ["studio/app/src/foo.ts", "studio/app/jest.config.ts"], "studio/app/")
        self.assertIsNone(subset)
        self.assertIn("jest.config.ts", reason)

    def test_package_json_descarta_el_subset(self) -> None:
        subset, reason = test_subset.map_jest_subtree(
            ["studio/app/package.json"], "studio/app/")
        self.assertIsNone(subset)
        self.assertNotEqual(reason, "no-aplica")

    def test_sin_archivos_del_subarbol_es_no_aplica(self) -> None:
        subset, reason = test_subset.map_jest_subtree(
            ["orchestrator/tests/unit/test_foo.py"], "studio/app/")
        self.assertIsNone(subset)
        self.assertEqual(reason, "no-aplica")


class PlanCombinedTests(unittest.TestCase):
    """Ambos subárboles tocados a la vez, y ninguno tocado — sobre una lista
    de `Subarbol` inyectada, nunca cableada en el código (CA-09)."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test-subset-plan-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.root = Path(self._tmp)
        self.subarboles = [ORCH, STUDIO]

    def test_ambos_subarboles_tocados_emiten_ambos_comandos(self) -> None:
        _make_repo(self.root, ["unit/test_foo.py"])
        results = test_subset.plan(
            [
                "orchestrator/tests/unit/test_foo.py",
                "studio/app/src/foo.ts",
            ], self.root, self.subarboles)
        by_subtree = {r.subtree: r for r in results}
        self.assertEqual(by_subtree["orchestrator"].status, "subset")
        self.assertIn("tests/unit/test_foo.py", by_subtree["orchestrator"].command)
        self.assertEqual(by_subtree["studio/app"].status, "subset")
        self.assertIn("src/foo.ts", by_subtree["studio/app"].command)

    def test_ningun_archivo_tocado_en_ninguno_de_los_dos_es_no_aplica_en_ambos(
        self,
    ) -> None:
        # `studio/package.json` es de un subárbol distinto (`studio/`, no
        # `studio/app/`) — ninguno de los dos subárboles configurados lo ve
        # tocado.
        results = test_subset.plan(["studio/package.json"], self.root, self.subarboles)
        by_subtree = {r.subtree: r for r in results}
        self.assertEqual(by_subtree["orchestrator"].status, "no-aplica")
        self.assertEqual(by_subtree["studio/app"].status, "no-aplica")

    def test_no_tocar_ninguno_no_es_incumplimiento_no_lanza_excepcion(self) -> None:
        try:
            results = test_subset.plan([], self.root, self.subarboles)
        except Exception as exc:  # pragma: no cover - documents the guarantee
            self.fail(f"plan([]) no debe fallar nunca: {exc}")
        self.assertTrue(all(r.status == "no-aplica" for r in results))


class CommandBuildingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test-subset-cmd-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.root = Path(self._tmp)

    def test_comando_de_fallback_de_pytest_es_la_corrida_completa(self) -> None:
        result = test_subset.subtree_result(["orchestrator/pyproject.toml"], self.root, ORCH)
        self.assertEqual(result.status, "fallback")
        self.assertEqual(result.command, "cd orchestrator && uv run pytest")

    def test_comando_de_fallback_de_jest_es_npm_test(self) -> None:
        result = test_subset.subtree_result(["studio/app/package.json"], self.root, STUDIO)
        self.assertEqual(result.status, "fallback")
        self.assertEqual(result.command, "cd studio/app && npm test")

    def test_comando_de_subset_de_jest_usa_find_related_tests(self) -> None:
        result = test_subset.subtree_result(["studio/app/src/foo.ts"], self.root, STUDIO)
        self.assertIn("--findRelatedTests", result.command)
        self.assertIn("src/foo.ts", result.command)

    def test_comando_de_subset_de_pytest_usa_rutas_relativas_al_subarbol(
        self,
    ) -> None:
        _make_repo(self.root, ["unit/test_foo.py"])
        result = test_subset.subtree_result(
            ["orchestrator/tests/unit/test_foo.py"], self.root, ORCH)
        self.assertEqual(result.command, "cd orchestrator && uv run pytest -m \"\" tests/unit/test_foo.py")

    def test_comando_de_subset_de_pytest_anula_el_filtro_de_integration_de_addopts(
        self,
    ) -> None:
        """Regresión (gate de código del validador de origen): sin el
        `-m ""`, un archivo tocado marcado `integration` produciría un subset
        que deselecciona el 100% de sus propios tests ("no tests ran"),
        violando el invariante de que el subset es superconjunto o igual del
        test del archivo tocado. `-m` es "último valor gana" en pytest, así
        que este `-m ""` neutraliza cualquier `-m "not integration"` que el
        `addopts` del destino inyecte por defecto."""
        _make_repo(self.root, ["test_mongo_integration.py"])
        result = test_subset.subtree_result(
            ["orchestrator/tests/test_mongo_integration.py"], self.root, ORCH)
        self.assertIn('-m ""', result.command)


class LoadSubarbolesTests(unittest.TestCase):
    """CA-09: `subarboles_test` se resuelve desde `.spec/protocolo-datos.yaml`
    del destino, no está cableado en el código de `test_subset.py`."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="test-subset-config-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.root = Path(self._tmp)

    def test_no_orchestrator_ni_studio_app_cableados_en_el_codigo(self) -> None:
        import inspect
        source = inspect.getsource(test_subset)
        self.assertNotIn("orchestrator/", source)
        self.assertNotIn("studio/app/", source)

    def test_resuelve_subarboles_desde_protocolo_datos_yaml(self) -> None:
        (self.root / ".spec").mkdir(parents=True)
        (self.root / ".spec" / "protocolo-datos.yaml").write_text(
            "subarboles_test:\n"
            "  - ruta: mi-subarbol/\n"
            "    runner: pytest\n"
            "    comando_completo: cd mi-subarbol && python3 -m pytest\n",
            encoding="utf-8")
        subarboles = test_subset.load_subarboles(None, self.root)
        self.assertEqual(len(subarboles), 1)
        self.assertEqual(subarboles[0].ruta, "mi-subarbol/")
        self.assertEqual(subarboles[0].runner, "pytest")

    def test_falla_explicito_sin_protocolo_datos_yaml(self) -> None:
        with self.assertRaises(test_subset.ProtocoloDatosError):
            test_subset.load_subarboles(None, self.root)


if __name__ == "__main__":
    unittest.main()
