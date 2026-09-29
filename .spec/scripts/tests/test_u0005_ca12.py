"""CA-12 — Post-U-0009 contract sobre ``.spec/_plantillas/``.

U-0009 retira tres plantillas de la carpeta ``_plantillas/`` del kit;
``mandato.md`` queda como la única plantilla de mandato y es **modificada
por U-0009 mismo** (CA-04). Por construcción, ya no aplica la
"byte-identidad durante la corrida de esta unidad" que la prosa original
de U-0005 verificaba: el contrato post-U-0009 pasa a ser:

1. La lista de paths excluidos del verificador de drift se reduce a la
   única plantilla que U-0005 no toca y U-0009 sí.
2. Esa plantilla existe en disco (defensa contra borrado accidental).
3. Esa plantilla referencia ``.spec/SUPERVISADO.md`` § 1.1 (anchor
   canónico introducido por U-0009 CA-04 — defensa contra una regresión
   que revierta la consolidación de ``mandato.md``).
"""

from __future__ import annotations

import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parents[2]

EXCLUDED_PATHS = [
    ".spec/_plantillas/mandato.md",
]


class ExcludedTemplatesByteIdenticalTests(unittest.TestCase):

    def test_u0005_ca12_excluded_paths_set_is_complete(self) -> None:
        # Defensa contra cambio silencioso de los paths cubiertos.
        # Post-U-0009: solo ``mandato.md`` queda como única plantilla de mandato.
        self.assertEqual(
            set(EXCLUDED_PATHS),
            {".spec/_plantillas/mandato.md"},
        )

    def test_u0005_ca12_mandato_template_exists(self) -> None:
        # Tras U-0009, ``mandato.md`` debe seguir presente en ``.spec/_plantillas/``.
        path = REPO_ROOT / ".spec" / "_plantillas" / "mandato.md"
        self.assertTrue(
            path.is_file(),
            f"CA-12: ``mandato.md`` debe existir post-U-0009 en {path}",
        )

    def test_u0005_ca12_mandato_template_no_regression_marker(self) -> None:
        # Marca semántica de la consolidación de ``mandato.md``: una vez
        # aplicado U-0009, el archivo contiene un anchor canónico que apunta a
        # ``.spec/SUPERVISADO.md`` § 1.1. Si ese anchor desaparece, la
        # consolidación está revertida — y este test falla.
        path = REPO_ROOT / ".spec" / "_plantillas" / "mandato.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            ".spec/SUPERVISADO.md", text,
            "CA-12: ``mandato.md`` debe referenciar ``.spec/SUPERVISADO.md`` "
            "(anchor canónico introducido por U-0009 CA-04).",
        )


if __name__ == "__main__":
    unittest.main()