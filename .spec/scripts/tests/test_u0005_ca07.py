"""CA-07 — Paths permitidos bajo ``.spec/_plantillas/``.

``ls .spec/_plantillas/`` debe ser igual a 8 — los 7 templates que este spec
puede tocar + ``mandato.md`` (única plantilla de mandato vigente tras
U-0009; las otras 3 plantillas del scope original de U-0005 son retiradas
por U-0009 como prerrequisito). El conjunto ``ALLOWED_PATHS`` codifica esa
lista vigente.

Verificación por ``git status --short .spec/_plantillas/``: cada path listado
debe pertenecer a la lista permitida.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parents[2]

ALLOWED_PATHS = {
    # Las 7 plantillas que este spec puede tocar.
    "_estado.yaml",
    "spec.md",
    "plan.md",
    "tasks.md",
    "research.md",
    "bitacora.md",
    "paquete-aprobacion.md",
    # La única plantilla de mandato vigente tras U-0009.
    "mandato.md",
}


def _git_status_plantillas() -> list[str]:
    """Returns the set of ``git status --short`` paths under
    ``.spec/_plantillas/``, basenames stripped of the dir prefix."""
    result = subprocess.run(
        ["git", "status", "--short", ".spec/_plantillas/"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    paths = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        # git status short format: `XY path` (XY is two status chars + space).
        m = re.match(r"^[!?AMDURC]{2}\s+(.+)$", line)
        if not m:
            continue
        path = m.group(1).strip()
        if path.startswith(".spec/_plantillas/"):
            paths.append(Path(path).name)
        else:
            # Possibly renamed/copied with arrow: `R  old -> new`
            for part in path.split(" -> "):
                if part.startswith(".spec/_plantillas/"):
                    paths.append(Path(part).name)
    return paths


class PlantillasAllowedPathsTests(unittest.TestCase):

    def test_u0005_ca07_total_plantillas_count_is_8(self) -> None:
        names = {p.name for p in (REPO_ROOT / ".spec" / "_plantillas").iterdir()}
        self.assertEqual(
            len(names), 8,
            f"deben haber 8 plantillas, encontré {len(names)}: {sorted(names)}",
        )
        self.assertEqual(names, ALLOWED_PATHS,
                         "el conjunto de plantillas debe coincidir exactamente "
                         "con las 7 en alcance + la única plantilla de mandato "
                         "vigente (post-U-0009)")

    def test_u0005_ca07_git_status_lists_only_allowed_paths(self) -> None:
        paths = _git_status_plantillas()
        offenders = [p for p in paths if p not in ALLOWED_PATHS]
        self.assertEqual(
            offenders, [],
            f"CA-07: paths inesperados bajo .spec/_plantillas/: {offenders}. "
            f"Solo se permiten: {sorted(ALLOWED_PATHS)}",
        )


if __name__ == "__main__":
    unittest.main()