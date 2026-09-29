"""CA-08 — Tamaño y contrato de `_estado.yaml` plantilla.

Tres chequeos:

1. `wc -l .spec/_plantillas/_estado.yaml | awk '{print $1}'` ≤ 100.
2. `yaml.safe_load(open(...))` parseable sin excepción.
3. El set de claves top-level coincide con ``EXPECTED_TOP_LEVEL_KEYS``.

El tercer chequeo protege el contrato del campo aunque cambie la prosa
alrededor (riesgo del plan § "G2 reduzca `_estado.yaml` por debajo del
contrato mínimo").
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parents[2]

PLANTILLA = REPO_ROOT / ".spec" / "_plantillas" / "_estado.yaml"

EXPECTED_TOP_LEVEL_KEYS = {
    "id",
    "titulo",
    "dueño",
    "carril",
    "fase",
    "estado",
    "creado",
    "actualizado",
    "governance_refs",
    "comando_validacion",
    "modo",
    "riesgo",
    "perfil",
    "modo_conversion",
    "aprobacion_paquete",
    "modelo_ejecucion",
    "gates",
    "depende_de",
    "mandato",
    "validaciones_mandato",
}

MAX_LINES = 100


def _wc_l(path: Path) -> int:
    """Mimic ``wc -l path`` — return the number of lines (``\n`` count)."""
    result = subprocess.run(
        ["wc", "-l", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.split()[0])


def _yaml_load(path: Path) -> dict:
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class EstadoPlantillaTests(unittest.TestCase):

    def test_u0005_ca08_line_count_within_budget(self) -> None:
        n = _wc_l(PLANTILLA)
        self.assertLessEqual(
            n, MAX_LINES,
            f"CA-08: _estado.yaml tiene {n} líneas, máximo {MAX_LINES}",
        )

    def test_u0005_ca08_yaml_parseable(self) -> None:
        try:
            data = _yaml_load(PLANTILLA)
        except Exception as e:
            self.fail(f"yaml.safe_load falló: {e}")
        self.assertIsInstance(data, dict)

    def test_u0005_ca08_top_level_keys_match_expected(self) -> None:
        data = _yaml_load(PLANTILLA)
        actual = set(data.keys())
        missing = EXPECTED_TOP_LEVEL_KEYS - actual
        extra = actual - EXPECTED_TOP_LEVEL_KEYS
        self.assertEqual(
            missing, set(),
            f"CA-08: faltan claves top-level en _estado.yaml: {missing}",
        )
        self.assertEqual(
            extra, set(),
            f"CA-08: _estado.yaml tiene claves inesperadas: {extra}",
        )

    def test_u0005_ca08_max_lines_constant(self) -> None:
        self.assertEqual(
            MAX_LINES, 100,
            "el tope de líneas de la unidad (≤100) debe permanecer constante",
        )


if __name__ == "__main__":
    unittest.main()