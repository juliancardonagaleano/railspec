"""CA-09 — Comentarios inline en `_estado.yaml` redirigidos al README.

Parsea `_estado.yaml` **sin PyYAML** (vía `_common.field` / `_common.top_level_block`)
— consistente con `validate_mode_conversion.py` y `validate_gate_budget.py`
(riesgo del plan § "sdd-retomar.py parsee comentarios como datos"). Busca
bloques de comentarios ``#`` consecutivos de ≥ 3 líneas; cada bloque debe
referenciar al README (`.spec/README.md § ...`) **o** ser reemplazado por
una sola línea explicativa.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent
sys.path.insert(0, str(SCRIPTS))

import _common  # noqa: E402

REPO_ROOT = SCRIPTS.parents[1]
PLANTILLA = REPO_ROOT / ".spec" / "_plantillas" / "_estado.yaml"

# Cada bloque de comentarios `#` consecutivos de ≥ 3 líneas debe contener
# una referencia al README o ser de una sola línea. Esto protege que la
# prosa de protocolo no se duplique inline.
README_REFERENCE = re.compile(r"\.spec/README\.md")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _comment_blocks(text: str) -> list[tuple[int, list[str]]]:
    """Devuelve ``[(start_line, [comment_line, ...])]`` para cada bloque
    de comentarios ``#`` consecutivos (al menos 1 línea)."""
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] = []
    current_start = 0
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            if not current:
                current_start = i
            current.append(stripped)
        else:
            if current:
                blocks.append((current_start, current))
                current = []
    if current:
        blocks.append((current_start, current))
    return blocks


class EstadoPlantillaInlineCommentsTests(unittest.TestCase):

    def test_u0005_ca09_estado_yaml_parses_without_pyyaml(self) -> None:
        """Garantiza el contrato de lectura via `_common` (sin PyYAML)."""
        text = _read_text(PLANTILLA)
        # Verifica que `_common.field`/`_common.top_level_block` siguen
        # funcionando tras la reducción.
        self.assertEqual(_common.field(text, "id"), "NNNN-slug")
        self.assertEqual(_common.field(text, "modo"), "interactivo")
        self.assertEqual(_common.field(text, "riesgo"), "medio")
        self.assertEqual(_common.field(text, "perfil"), "estandar")
        self.assertEqual(_common.field(text, "fase"), "spec")
        self.assertEqual(_common.field(text, "estado"), "en-progreso")
        self.assertEqual(_common.field(text, "creado"), "")
        self.assertEqual(_common.field(text, "actualizado"), "")
        self.assertEqual(_common.field(text, "mandato"), "")
        self.assertEqual(_common.field(text, "comando_validacion"), "")

    def test_u0005_ca09_no_3line_comment_block_without_readme_anchor(self) -> None:
        text = _read_text(PLANTILLA)
        offenders: list[tuple[int, list[str]]] = []
        for start, block in _comment_blocks(text):
            if len(block) < 3:
                continue
            block_text = "\n".join(block)
            if README_REFERENCE.search(block_text):
                continue
            offenders.append((start, block))
        self.assertEqual(
            offenders, [],
            "CA-09: bloques de comentarios `#` de ≥ 3 líneas sin anchor al "
            "README en `_estado.yaml` (cada bloque debe redirigir a "
            ".spec/README.md § ... o reemplazarse por una sola línea "
            f"explicativa): {offenders}",
        )

    def test_u0005_ca09_inline_comment_blocks_only_with_readme_reference(self) -> None:
        """Variante estricta: cualquier bloque de ≥ 2 líneas debe referenciar
        al README o al ``.spec/`` (no prosa suelta)."""
        text = _read_text(PLANTILLA)
        offenders: list[tuple[int, list[str]]] = []
        for start, block in _comment_blocks(text):
            if len(block) < 2:
                continue
            block_text = "\n".join(block)
            if ".spec/" in block_text:
                continue
            offenders.append((start, block))
        self.assertEqual(
            offenders, [],
            f"CA-09: bloques de comentarios `#` de ≥ 2 líneas sin "
            f"referencia a `.spec/`: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()