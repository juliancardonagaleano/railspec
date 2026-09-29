"""CA-05 — Prosa duplicada entre plantillas.

Cada bloque de prosa de ≥ 5 líneas consecutivas en una plantilla debe vivir
solo en esa plantilla — la duplicación se mueve a `.spec/README.md` (la
fuente única canónica) y el resto referencia con un anchor. Para
``_estado.yaml`` (YAML, no prosa), el "bloque" son líneas consecutivas de
comentario ``#`` alineado con la semántica de CA-09.

El test excluye explícitamente:

- Marcadores de plantilla (``<...>``, ``NNNN-slug``) — son placeholders, no
  prosa a centralizar.
- Bloques entre asteriscos (``**foo**``) que son ejemplos de payload.
- Headers, tablas, listas y líneas vacías (no son prosa continua).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parents[2]
PLANTILLAS = REPO_ROOT / ".spec" / "_plantillas"

MD_SCOPED = [
    PLANTILLAS / "spec.md",
    PLANTILLAS / "plan.md",
    PLANTILLAS / "tasks.md",
    PLANTILLAS / "research.md",
    PLANTILLAS / "bitacora.md",
    PLANTILLAS / "paquete-aprobacion.md",
]

YAML_SCOPED = [PLANTILLAS / "_estado.yaml"]


# --- Extractor de bloques de prosa ---------------------------------------------

_HEADER_RE = re.compile(r"^\s*#{1,6}\s")
_TABLE_RE = re.compile(r"^\s*\|.*\|")
_LIST_RE = re.compile(r"^\s*[-*+]\s")
_ORDERED_LIST_RE = re.compile(r"^\s*\d+\.\s")
_BLOCKQUOTE_RE = re.compile(r"^\s*>")
_BLANK_RE = re.compile(r"^\s*$")
_PLACEHOLDER_RE = re.compile(r"<[^>]+>|NNNN-[a-z0-9-]+")
_INLINE_CODE_RE = re.compile(r"`[^`]+`")


def _is_prose_line(line: str) -> bool:
    """Una línea es "prosa" si no es header, tabla, lista, blockquote o vacía."""
    if _BLANK_RE.match(line):
        return False
    if _HEADER_RE.match(line):
        return False
    if _TABLE_RE.match(line):
        return False
    if _LIST_RE.match(line):
        return False
    if _ORDERED_LIST_RE.match(line):
        return False
    if _BLOCKQUOTE_RE.match(line):
        return False
    return True


def _normalize(line: str) -> str:
    """Normaliza una línea para el diff: lowercase, sin inline-code, sin
    placeholders, whitespace colapsado."""
    s = line.strip().lower()
    s = _INLINE_CODE_RE.sub("", s)
    s = _PLACEHOLDER_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_prose_blocks(text: str, min_lines: int = 5) -> list[str]:
    """Devuelve la lista de bloques de prosa normalizados de ≥ ``min_lines``
    líneas consecutivas (sin contar headers/tablas/listas/etc.)."""
    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for ln in lines:
        if _is_prose_line(ln):
            current.append(_normalize(ln))
        else:
            if len(current) >= min_lines:
                blocks.append(current)
            current = []
    if len(current) >= min_lines:
        blocks.append(current)
    return ["\n".join(b) for b in blocks]


def extract_yaml_comment_blocks(text: str, min_lines: int = 5) -> list[str]:
    """Para ``_estado.yaml``: bloques de comentarios ``#`` consecutivos de
    ≥ ``min_lines`` líneas (no anidados — solo líneas que empiezan por ``#``).
    """
    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("#"):
            current.append(stripped)
        else:
            if len(current) >= min_lines:
                blocks.append(current)
            current = []
    if len(current) >= min_lines:
        blocks.append(current)
    return ["\n".join(b) for b in blocks]


class DuplicationTests(unittest.TestCase):

    def test_u0005_ca05_no_md_template_has_duplicate_prose_block(self) -> None:
        per_template: dict[str, list[str]] = {}
        for path in MD_SCOPED:
            text = path.read_text(encoding="utf-8")
            per_template[path.name] = extract_prose_blocks(text)

        names = sorted(per_template.keys())
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                blocks_a = set(per_template[a])
                blocks_b = set(per_template[b])
                common = blocks_a & blocks_b
                if common:
                    sample = next(iter(common))
                    self.fail(
                        f"CA-05: bloque de prosa duplicado entre {a} y {b}: "
                        f"\n--- muestra ---\n{sample}\n--- fin ---"
                    )

    def test_u0005_ca05_yaml_state_template_no_5line_comment_block(self) -> None:
        """Para ``_estado.yaml`` no debe quedar ningún bloque de comentarios
        ``#`` de ≥ 5 líneas consecutivas (alineado con CA-09)."""
        for path in YAML_SCOPED:
            text = path.read_text(encoding="utf-8")
            blocks = extract_yaml_comment_blocks(text, min_lines=5)
            self.assertEqual(
                blocks, [],
                f"CA-05: {path.name} tiene bloques de comentarios `#` de "
                f"≥ 5 líneas consecutivas — la prosa debe vivir en "
                f".spec/README.md, no inline. Encontrados: {len(blocks)}",
            )

    def test_u0005_ca05_yaml_state_template_comment_blocks_lt_3(self) -> None:
        """CA-09: ningún bloque ``#`` de ≥ 3 líneas sin anchor al README."""
        for path in YAML_SCOPED:
            text = path.read_text(encoding="utf-8")
            blocks = extract_yaml_comment_blocks(text, min_lines=3)
            offenders = [b for b in blocks if ".spec/README.md" not in b]
            self.assertEqual(
                offenders, [],
                f"CA-09: {path.name} tiene {len(offenders)} bloques de "
                f"comentarios `#` de ≥ 3 líneas sin anchor al README. "
                f"Cada bloque debe reemplazarse por una referencia a "
                f".spec/README.md o por una sola línea explicativa.",
            )

    def test_u0005_ca05_paquete_aprobacion_has_no_perfil_legado(self) -> None:
        """El reemplazo de T7 debe haber eliminado el bloque de 'Perfil de
        esfuerzo' que duplicaba la semántica de `.spec/perfiles.yaml`."""
        text = (PLANTILLAS / "paquete-aprobacion.md").read_text(encoding="utf-8")
        self.assertNotIn(
            "`/sdd-perfil <nombre>`", text,
            "el bloque heredado 'Perfil de esfuerzo' debe haberse reemplazado "
            "por una referencia al README § Los cuatro modos o al header de "
            ".spec/perfiles.yaml",
        )


if __name__ == "__main__":
    unittest.main()