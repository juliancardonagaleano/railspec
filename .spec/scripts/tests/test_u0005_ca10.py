"""CA-10 — Bloque "Complejidad" en `plan.md` ≤ 5 líneas no-vacías.

Extrae el bloque entre ``> Si el cambio no admite paralelismo real`` y el
siguiente ``##`` en `.spec/_plantillas/plan.md`, cuenta líneas no-vacías y
verifica ≤ 5 (hoy era 16 — la compresión de T6 debe haberlo reducido).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parents[2]

PLANTILLA = REPO_ROOT / ".spec" / "_plantillas" / "plan.md"

MAX_NON_EMPTY_LINES = 5
START_MARKER = "> Si el cambio no admite paralelismo real"
LITERAL_COMPLEJIDAD = "Complejidad: "
README_REFERENCE = "Reglas del fan-out"


def _extract_complejidad_block(text: str) -> str:
    start = text.find(START_MARKER)
    if start == -1:
        return ""
    rest = text[start:]
    end = rest.find("\n## ")
    return rest[:end] if end != -1 else rest


class PlanComplejidadBlockTests(unittest.TestCase):

    def test_u0005_ca10_block_under_max_lines(self) -> None:
        text = PLANTILLA.read_text(encoding="utf-8")
        block = _extract_complejidad_block(text)
        self.assertTrue(
            block,
            "el bloque 'Complejidad' no se localizó en plan.md",
        )
        non_empty = [l for l in block.splitlines() if l.strip()]
        self.assertLessEqual(
            len(non_empty), MAX_NON_EMPTY_LINES,
            f"CA-10: bloque 'Complejidad' tiene {len(non_empty)} líneas "
            f"no-vacías, máximo {MAX_NON_EMPTY_LINES}",
        )

    def test_u0005_ca10_preserves_complejidad_literals(self) -> None:
        text = PLANTILLA.read_text(encoding="utf-8")
        block = _extract_complejidad_block(text)
        # Debe mantener el literal de los valores válidos.
        self.assertIn("estandar", block,
                      "el bloque debe mantener el literal 'estandar'")
        self.assertIn("complejo", block,
                      "el bloque debe mantener el literal 'complejo'")
        self.assertIn(LITERAL_COMPLEJIDAD, block,
                      "el bloque debe contener el prefijo 'Complejidad: '")

    def test_u0005_ca10_references_readme(self) -> None:
        """El bloque comprimido debe apuntar al README § Reglas del fan-out."""
        text = PLANTILLA.read_text(encoding="utf-8")
        block = _extract_complejidad_block(text)
        self.assertIn(README_REFERENCE, block,
                      "el bloque comprimido debe referenciar la sección "
                      "'Reglas del fan-out' del README")


if __name__ == "__main__":
    unittest.main()