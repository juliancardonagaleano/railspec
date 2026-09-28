"""A unit-level open stop (`unidad:` ≠ `todas`) keeps the mandate `aprobado`;
only a mandate-level one demands `parado` (PARADAS-SUPERVISADO.md preamble)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _common  # noqa: E402
import validate_mandate as vm  # noqa: E402

_TEXT = ("# fixture\n\n## Estado\n\n- estado: aprobado\n\n## Paradas\n\n"
         "### 2026-09-24T20:50:00Z\n\n- unidad: {unit}\n- disparador: gate `escalado`\n"
         "- causa: decisión humana pendiente\n- invalida-el-objetivo: no\n- sesion: s\n")


class UnitLevelStopTest(unittest.TestCase):
    def _codes(self, unit: str) -> list[str]:
        _common.reset()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.md"
            path.write_text(_TEXT.format(unit=unit), encoding="utf-8")
            vm.check_stops(vm.Mandate(path, "plan", "fixture", Path(tmp) / "units"))
        return [code for code, _, _ in _common.FAILURES]

    def test_unit_level_stop_keeps_mandate_approved(self) -> None:
        self.assertEqual(self._codes("`0005-paquete-de-entrega`"), [])

    def test_mandate_level_stop_requires_parado(self) -> None:
        self.assertEqual(self._codes("todas"), ["parada-abierta-sin-estado-parado"])
        self.assertEqual(self._codes(""), ["parada-abierta-sin-estado-parado"])


if __name__ == "__main__":
    unittest.main()
