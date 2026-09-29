"""CA-13 — Auto-trazabilidad de los verificadores U-0005.

Itera sobre CA-02..CA-12 (CA-01 no requiere test propio — su verificación
es literal `grep -n '^# nomenclatura' .spec/README.md`). Verifica que cada
CA-NN tiene al menos un test coleccionable vía
``python3 -m pytest .spec/scripts/tests --collect-only -k u0005_ca<NN>``.

Esta es la versión auto-verificable de CA-13: el test prueba que existe al
menos un test por CA, ejercitando el contrato de auto-trazabilidad del
spec.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parents[2]

CA_NUMBERS = list(range(2, 13))  # CA-02 .. CA-12 inclusive.


def _collect_u0005_ca(nn: int) -> int:
    """Return the number of test items collected by ``-k u0005_ca<NN>``."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            ".spec/scripts/tests",
            "--collect-only",
            "-q",
            "-k",
            f"u0005_ca{nn:02d}",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    # `--collect-only` counts lines matching `<Module>::<Class>::<test>`.
    return sum(
        1 for ln in result.stdout.splitlines()
        if re.match(r"^\s+\<.*\>\s*::", ln) is None
        and "::" in ln
        and f"u0005_ca{nn:02d}" in ln
    )


def _all_collected_u0005() -> int:
    """Return total number of test items collected by ``-k u0005``."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            ".spec/scripts/tests",
            "--collect-only",
            "-q",
            "-k",
            "u0005",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    n = 0
    for ln in result.stdout.splitlines():
        if "::" in ln and "u0005" in ln and not ln.startswith("="):
            n += 1
    return n


class AutoTraceabilityTests(unittest.TestCase):

    def test_u0005_ca13_each_ca_02_through_12_has_at_least_one_test(self) -> None:
        missing: list[int] = []
        for nn in CA_NUMBERS:
            with self.subTest(ca=f"CA-{nn:02d}"):
                count = _collect_u0005_ca(nn)
                if count < 1:
                    missing.append(nn)
        self.assertEqual(
            missing, [],
            f"CA-13: los siguientes CAN no tienen tests propios "
            f"coleccionables con -k u0005_ca<NN>: {missing}",
        )

    def test_u0005_ca13_at_least_12_tests_listed(self) -> None:
        """El spec exige ≥ 12 tests listados (CA-02..CA-12 = 11 + al menos
        uno más — el propio CA-13). Verificación literal del spec."""
        n = _all_collected_u0005()
        self.assertGreaterEqual(
            n, 12,
            f"CA-13: --collect-only -k u0005 debe listar ≥ 12 items, "
            f"encontré {n}",
        )


if __name__ == "__main__":
    unittest.main()