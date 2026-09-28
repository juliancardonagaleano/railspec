"""Regression guard for the indented-`mandato:` change to the validator.

Unit 0132 T1 (CA-12 a-d): `validate_mandate.py:358` now accepts `mandato:` at
any indent level. The two cases pin each sub-criterion (accepted when
indented, still rejected inside a block scalar) so the original failure mode
does not return, while `field()`'s structural fallback still rejects when
`mandato:` is only inside a block scalar or a sibling block. Both seeds are
inline (no dependency on any fixture tree).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
VALIDATOR = REPO / ".spec" / "scripts" / "validate_mandate.py"


def _run_validator(unit_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("IARK_GUARD_EXEMPT_TREE", None)
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--unidad", str(unit_dir)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class MandatoIndentTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="mandato-indent-")).resolve()
        self.unit = self._tmp / "0000-unit"
        self.unit.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_estado(self, body: str) -> Path:
        (self.unit / "_estado.yaml").write_text(body, encoding="utf-8")
        return self.unit

    def test_mandato_indented_under_unidad_accepted(self) -> None:
        unit = self._write_estado("unidad:\n  mandato: foo\n  fase: plan\n")
        result = _run_validator(unit)
        combined = result.stdout + result.stderr
        self.assertNotIn(
            "unidad-supervisado-sin-mandato",
            combined,
            "the indented-`mandato:` regression has resurfaced:\n" + combined,
        )

    def test_mandato_in_block_scalar_rejected(self) -> None:
        body = (
            "description: |\n"
            "  El mandato: foo\n"
            "  Mas texto\n"
        )
        unit = self._write_estado(body)
        result = _run_validator(unit)
        combined = result.stdout + result.stderr
        self.assertNotEqual(
            result.returncode, 0,
            "the block-scalar case should still reject:\n" + combined,
        )
        self.assertIn(
            "unidad-supervisado-sin-mandato",
            combined,
            "expected `unidad-supervisado-sin-mandato` because `mandato:` is "
            "inside the `description: |` block scalar, not at line start; "
            "got:\n" + combined,
        )


if __name__ == "__main__":
    unittest.main()
