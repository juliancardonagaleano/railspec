"""CA-11 — Centralización de las convenciones de concurrencia.

Verifica que:

1. ``grep -F 'Convención de merge (unidad 0098)' .spec/_plantillas/bitacora.md``
   retorna 0.
2. ``grep -F '0083/0084' .spec/_plantillas/_estado.yaml`` retorna 0.
3. El anchor ``.spec/README.md § concurrencia`` aparece en
   `.spec/_plantillas/bitacora.md` y `.spec/_plantillas/_estado.yaml`.

El spec también exige centralización de la regla 0083/0084 (CA-11); la
verificación persistente es que **ambos** archivos referencian el README §
concurrencia (no que el README contenga la regla — eso ya está probado por
CA-06).
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parents[2]

BITACORA = REPO_ROOT / ".spec" / "_plantillas" / "bitacora.md"
ESTADO = REPO_ROOT / ".spec" / "_plantillas" / "_estado.yaml"
README = REPO_ROOT / ".spec" / "README.md"

CONCURRENCIA_ANCHOR = "## concurrencia"
README_REF = re.compile(r"\.spec/README\.md\s*§\s*concurrencia|README\.md#concurrencia")


def _count_grep(pattern: str, path: Path) -> int:
    result = subprocess.run(
        ["grep", "-F", "--", pattern, str(path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    return 0 if result.returncode != 0 else len(result.stdout.splitlines())


class ConcurrenciaCentralizationTests(unittest.TestCase):

    def test_u0005_ca11_bitacora_no_longer_has_merge_convention_phrase(self) -> None:
        hits = _count_grep("Convención de merge (unidad 0098)", BITACORA)
        self.assertEqual(
            hits, 0,
            f"CA-11: 'Convención de merge (unidad 0098)' aún aparece en "
            f"bitacora.md ({hits} hits) — debe haberse centralizado en "
            f".spec/README.md § concurrencia",
        )

    def test_u0005_ca11_estado_yaml_no_longer_has_0083_0084_rule(self) -> None:
        hits = _count_grep("0083/0084", ESTADO)
        self.assertEqual(
            hits, 0,
            f"CA-11: '0083/0084' aún aparece en _estado.yaml ({hits} hits) "
            f"— la regla debe referenciarse desde .spec/README.md § concurrencia",
        )

    def test_u0005_ca11_readme_has_concurrencia_section(self) -> None:
        text = README.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^## concurrencia\s*$")

    def test_u0005_ca11_bitacora_references_readme_concurrencia(self) -> None:
        text = BITACORA.read_text(encoding="utf-8")
        self.assertRegex(text, README_REF,
                         "bitacora.md debe referenciar "
                         ".spec/README.md § concurrencia")

    def test_u0005_ca11_estado_yaml_references_readme_concurrencia(self) -> None:
        text = ESTADO.read_text(encoding="utf-8")
        self.assertRegex(text, README_REF,
                         "_estado.yaml debe referenciar "
                         ".spec/README.md § concurrencia")


if __name__ == "__main__":
    unittest.main()