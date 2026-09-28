"""Tests for `.spec/scripts/validate_model_catalog.py` (CA-13).

Covers: every declared `modelo` inside the catalog produces no `WARN` line;
a model outside every profile's catalog (including the nested
`research.consolidacion` branch) produces exactly one `WARN` naming the unit
and the dotted key; both cases exit 0 (P-2 — report-only, never blocks).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS_DIR / "validate_model_catalog.py"

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

  profundo:
    roles:
      sdd-especificar-redactor: {modelo: fable}
      sdd-critico-profundo: {modelo: opus}
      sdd-refutador: {modelo: opus}
"""


def _run(unit_dir: Path, profiles_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--unit", str(unit_dir), "--profiles", str(profiles_path)],
        capture_output=True,
        text=True,
        check=False,
    )


class ValidateModelCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.profiles_path = self.tmp_path / "perfiles.yaml"
        self.profiles_path.write_text(SAMPLE_PROFILES, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_unit(self, name: str, modelo_ejecucion_block: str) -> Path:
        unit_dir = self.tmp_path / name
        unit_dir.mkdir()
        (unit_dir / "_estado.yaml").write_text(
            f"id: {name}\ntitulo: fixture\nfase: implement\nestado: en-progreso\n"
            f"perfil: estandar\nriesgo: alto\n{modelo_ejecucion_block}",
            encoding="utf-8",
        )
        return unit_dir

    def test_all_models_in_catalog_produces_no_warn(self) -> None:
        unit_dir = self._write_unit(
            "all-cataloged",
            "modelo_ejecucion:\n"
            "  research:\n"
            "    exploradores: {subagente: sdd-explorador, modelo: sonnet, effort: medium, perfil: estandar, instancias: 3, en: \"2026-09-24T09:00:00Z\"}\n"
            "    consolidacion: {subagente: inline, modelo: opus, perfil: estandar, en: \"2026-09-24T09:30:00Z\"}\n"
            "  especificar: {subagente: sdd-especificar-redactor, modelo: sonnet, effort: high, perfil: estandar, en: \"2026-09-24T10:00:00Z\"}\n"
            "  tareas: {subagente: sdd-tareas-redactor, modelo: haiku, perfil: estandar, en: \"2026-09-24T10:30:00Z\"}\n"
            "  implementar:\n"
            "    - {grupo: G1, subagente: sdd-implementador, modelo: sonnet, effort: high, perfil: estandar, complejidad: estandar}\n",
        )
        result = _run(unit_dir, self.profiles_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("WARN", result.stdout)

    def test_model_outside_catalog_warns_with_unit_and_key(self) -> None:
        unit_dir = self._write_unit(
            "out-of-catalog",
            "modelo_ejecucion:\n"
            "  research:\n"
            "    exploradores: {subagente: sdd-explorador, modelo: sonnet, effort: medium, perfil: estandar, instancias: 3, en: \"2026-09-24T09:00:00Z\"}\n"
            "    consolidacion: {subagente: inline, modelo: gpt-9-imaginary, perfil: estandar, en: \"2026-09-24T09:30:00Z\"}\n",
        )
        result = _run(unit_dir, self.profiles_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "WARN out-of-catalog: modelo_ejecucion.research.consolidacion.modelo="
            "gpt-9-imaginary no está en el catálogo de perfiles.yaml",
            result.stdout,
        )

    def test_gates_criticos_and_refutador_models_are_checked_too(self) -> None:
        unit_dir = self._write_unit(
            "gates-checked",
            "modelo_ejecucion: {}\n"
            "gates:\n"
            "  spec:\n"
            "    veredicto: refinado\n"
            "    iteraciones: 1\n"
            "    criticos:\n"
            "      - {lente: \"L1\", subagente: sdd-critico-profundo, modelo: gpt-9-imaginary, effort: high, perfil: estandar}\n"
            "    refutador:\n"
            "      - {subagente: sdd-refutador, modelo: sonnet, effort: high, perfil: estandar}\n",
        )
        result = _run(unit_dir, self.profiles_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gates.spec.criticos[0].modelo=gpt-9-imaginary", result.stdout)
        self.assertNotIn("gates.spec.refutador[0].modelo", result.stdout)

    def test_list_index_resets_between_sibling_lists(self) -> None:
        """`criticos[0]` and `refutador[0]`:
        both lists sit at the same indent inside `spec`, and the counter used
        to be keyed by indent alone, so `refutador`'s one item came out as
        `refutador[1]` (continuing `criticos`' own count) instead of `[0]`."""
        unit_dir = self._write_unit(
            "list-index-siblings",
            "modelo_ejecucion: {}\n"
            "gates:\n"
            "  spec:\n"
            "    veredicto: refinado\n"
            "    iteraciones: 1\n"
            "    criticos:\n"
            "      - {lente: \"L1\", subagente: sdd-critico-profundo, modelo: gpt-9-imaginary, effort: high, perfil: estandar}\n"
            "    refutador:\n"
            "      - {subagente: sdd-refutador, modelo: gpt-9-otra, effort: high, perfil: estandar}\n",
        )
        result = _run(unit_dir, self.profiles_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gates.spec.criticos[0].modelo=gpt-9-imaginary", result.stdout)
        self.assertIn("gates.spec.refutador[0].modelo=gpt-9-otra", result.stdout)

    def test_list_index_resets_between_sibling_lists_in_compact_style(self) -> None:
        """Same as above, but the `- {...}` items sit at the same column as
        their parent key (valid compact YAML that the writer never re-indents)."""
        unit_dir = self._write_unit(
            "list-index-siblings-compact",
            "modelo_ejecucion: {}\n"
            "gates:\n"
            "  codigo:\n"
            "    veredicto: refinado\n"
            "    iteraciones: 1\n"
            "    criticos:\n"
            "    - {lente: \"L1\", subagente: sdd-critico-profundo, modelo: gpt-9-imaginary, effort: high, perfil: estandar}\n"
            "    - {lente: \"L2\", subagente: sdd-critico-profundo, modelo: gpt-9-imaginary, effort: high, perfil: estandar}\n"
            "    refutador:\n"
            "    - {subagente: sdd-refutador, modelo: gpt-9-otra, effort: high, perfil: estandar}\n",
        )
        result = _run(unit_dir, self.profiles_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gates.codigo.criticos[0].modelo=gpt-9-imaginary", result.stdout)
        self.assertIn("gates.codigo.criticos[1].modelo=gpt-9-imaginary", result.stdout)
        self.assertIn("gates.codigo.refutador[0].modelo=gpt-9-otra", result.stdout)
        self.assertNotIn("refutador[2]", result.stdout)

    def test_list_index_resets_between_phases(self) -> None:
        unit_dir = self._write_unit(
            "list-index-phases",
            "modelo_ejecucion: {}\n"
            "gates:\n"
            "  spec:\n"
            "    veredicto: refinado\n"
            "    iteraciones: 1\n"
            "    criticos:\n"
            "      - {lente: \"L1\", subagente: sdd-critico-profundo, modelo: gpt-9-imaginary, effort: high, perfil: estandar}\n"
            "  plan:\n"
            "    veredicto: refinado\n"
            "    iteraciones: 1\n"
            "    criticos:\n"
            "      - {lente: \"L1\", subagente: sdd-critico-profundo, modelo: gpt-9-otra, effort: high, perfil: estandar}\n",
        )
        result = _run(unit_dir, self.profiles_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gates.spec.criticos[0].modelo=gpt-9-imaginary", result.stdout)
        self.assertIn("gates.plan.criticos[0].modelo=gpt-9-otra", result.stdout)

    def test_no_modelo_ejecucion_field_exits_zero_without_warn(self) -> None:
        unit_dir = self._write_unit("no-field", "")
        result = _run(unit_dir, self.profiles_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("WARN", result.stdout)

    def test_missing_state_file_exits_nonzero(self) -> None:
        unit_dir = self.tmp_path / "missing"
        unit_dir.mkdir()
        result = _run(unit_dir, self.profiles_path)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
