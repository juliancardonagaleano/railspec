"""CA-06 — Anchors reproducibles al README en plantillas re-declarantes.

Si una plantilla re-declara prosa que pertenece al protocolo (e.g. "Cómo se
cierra un ciclo SDD"), debe **referenciar** la sección canónica del
``.spec/README.md`` con un anchor reproducible en vez de re-declarar el texto.

La verificación busca anchors de la forma ``.spec/README.md § nomenclatura``
o ``§ concurrencia`` (o variantes ``README.md#nomenclatura``,
``README.md#concurrencia``) en las plantillas que el test de CA-05 confirmó
que NO contienen duplicación (el filtro natural: si pasó CA-05, no hay
prosa duplicada, así que los anchors presentes son referencias legítimas).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parents[2]
PLANTILLAS = REPO_ROOT / ".spec" / "_plantillas"
README_PATH = REPO_ROOT / ".spec" / "README.md"

# Plantillas que pueden re-declarar prosa del protocolo (las 7 del alcance).
IN_SCOPE = [
    PLANTILLAS / "_estado.yaml",
    PLANTILLAS / "spec.md",
    PLANTILLAS / "plan.md",
    PLANTILLAS / "tasks.md",
    PLANTILLAS / "research.md",
    PLANTILLAS / "bitacora.md",
    PLANTILLAS / "paquete-aprobacion.md",
]

ANCHOR_PATTERNS = [
    re.compile(r"\.spec/README\.md\s*§\s*concurrencia"),
    re.compile(r"\.spec/README\.md\s*§\s*nomenclatura"),
    re.compile(r"\.spec/README\.md\s*§\s*Los cuatro modos"),
    re.compile(r"\.spec/README\.md\s*§\s*Gates de validaci[oó]n"),
    re.compile(r"README\.md#concurrencia"),
    re.compile(r"README\.md#nomenclatura"),
    re.compile(r"README\.md#los-cuatro-modos"),
    re.compile(r"README\.md#reglas-del-fan-out"),
]


def _has_anchor(text: str) -> list[str]:
    """Devuelve la lista de anchors que aparecen en ``text``."""
    found = []
    for pat in ANCHOR_PATTERNS:
        for m in pat.finditer(text):
            found.append(m.group(0))
    return found


class ReproducibleAnchorsTests(unittest.TestCase):

    def test_u0005_ca06_readme_has_required_anchor_targets(self) -> None:
        text = README_PATH.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^## concurrencia\s*$")
        self.assertRegex(text, r"(?m)^# nomenclatura:\s*$")
        self.assertRegex(text, r"(?m)^## Los cuatro modos\s*$")
        self.assertRegex(text, r"(?m)^## Gates de validaci[oó]n\s*$")

    def test_u0005_ca06_estado_yaml_references_concurrencia(self) -> None:
        text = (PLANTILLAS / "_estado.yaml").read_text(encoding="utf-8")
        anchors = _has_anchor(text)
        self.assertTrue(
            any("concurrencia" in a for a in anchors),
            f"_estado.yaml debe referenciar `.spec/README.md § concurrencia`; "
            f"anchors encontrados: {anchors}",
        )

    def test_u0005_ca06_estado_yaml_references_modos_o_gates(self) -> None:
        text = (PLANTILLAS / "_estado.yaml").read_text(encoding="utf-8")
        anchors = _has_anchor(text)
        self.assertTrue(
            any("Los cuatro modos" in a or "Gates de validaci" in a for a in anchors),
            f"_estado.yaml debe referenciar `.spec/README.md § Los cuatro "
            f"modos` o `§ Gates de validación`; anchors: {anchors}",
        )

    def test_u0005_ca06_bitacora_references_concurrencia(self) -> None:
        text = (PLANTILLAS / "bitacora.md").read_text(encoding="utf-8")
        anchors = _has_anchor(text)
        self.assertTrue(
            any("concurrencia" in a for a in anchors),
            f"bitacora.md debe referenciar `.spec/README.md § concurrencia`; "
            f"anchors: {anchors}",
        )

    def test_u0005_ca06_plan_references_fan_out(self) -> None:
        text = (PLANTILLAS / "plan.md").read_text(encoding="utf-8")
        anchors = _has_anchor(text)
        self.assertTrue(
            any("reglas-del-fan-out" in a or "fan-out" in a for a in anchors),
            f"plan.md debe referenciar la sección de Reglas del fan-out "
            f"tras la compresión de T6; anchors: {anchors}",
        )

    def test_u0005_ca06_paquete_aprobacion_references_modos(self) -> None:
        text = (PLANTILLAS / "paquete-aprobacion.md").read_text(encoding="utf-8")
        anchors = _has_anchor(text)
        self.assertTrue(
            any("Los cuatro modos" in a or "perfiles.yaml" in a for a in anchors),
            f"paquete-aprobacion.md debe referenciar `.spec/README.md § Los "
            f"cuatro modos` o al header de perfiles.yaml tras T7; "
            f"anchors: {anchors}",
        )

    def test_u0005_ca06_every_modified_template_has_anchor(self) -> None:
        """Las plantillas modificadas por G2 (T4..T7) — que re-declaran
        prosa del protocolo — deben contener al menos un anchor al README.

        spec.md, tasks.md y research.md no se tocan en esta unidad (prosa
        única por plantilla) y por tanto no necesitan anchor."""
        modified = [
            PLANTILLAS / "_estado.yaml",
            PLANTILLAS / "bitacora.md",
            PLANTILLAS / "plan.md",
            PLANTILLAS / "paquete-aprobacion.md",
        ]
        for path in modified:
            text = path.read_text(encoding="utf-8")
            anchors = _has_anchor(text)
            with self.subTest(path=path.name):
                self.assertTrue(
                    anchors,
                    f"{path.name} no contiene ningún anchor reproducible al "
                    f"README — la consolidación de T4..T7 debería haber "
                    f"dejado al menos una referencia",
                )


if __name__ == "__main__":
    unittest.main()