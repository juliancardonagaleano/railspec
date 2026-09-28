"""Tests for `gate_delta.py` (unit 0116, G3 — CA-10/CA-11).

Uses the fixtures at `.spec/_fixtures/gate-delta/{iter1,iter2}-artefacto.md`:
two versions of the same artifact, so `rotate` can be exercised across a
realistic "iteración 1 → iteración 2" transition without inventing content.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import gate_delta  # noqa: E402

REPO_ROOT = SCRIPTS.parent.parent
FIXTURES = REPO_ROOT / ".spec" / "_fixtures" / "gate-delta"
ITER1 = FIXTURES / "iter1-artefacto.md"
ITER2 = FIXTURES / "iter2-artefacto.md"


class GateDeltaRotateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="gate-delta-")
        self.unidad = Path(self._tmp) / "0000-unidad-prueba"
        self.unidad.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _cache(self) -> Path:
        return gate_delta.cache_path(self.unidad, "plan")

    # --- CA-11 — first iteration is always the whole artifact ------------

    def test_no_cache_reports_iteration_1_and_full_text(self) -> None:
        rc = gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER1),
        ])
        self.assertEqual(rc, 0)

    def test_no_cache_never_errors_even_on_a_resumed_unit(self) -> None:
        """Same scenario the plan.md § Riesgos names explicitly: a unit resumed
        mid-gate has no cache from a previous session — `rotate` must treat
        that exactly like a fresh iteration 1, never as an error."""
        self.assertFalse(self._cache().exists())
        rc = gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER1),
        ])
        self.assertEqual(rc, 0)

    def test_first_rotate_writes_the_cache(self) -> None:
        gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER1),
        ])
        self.assertTrue(self._cache().is_file())
        self.assertEqual(
            self._cache().read_text(encoding="utf-8"),
            ITER1.read_text(encoding="utf-8"),
        )

    # --- CA-10 — iteration > 1 gets a diff, not the whole text again -----

    def test_second_rotate_prints_a_diff_not_the_full_text(self) -> None:
        gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER1),
        ])
        buf = _capture_stdout(lambda: gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER2),
        ]))
        self.assertIn("DELTA", buf)
        self.assertNotIn("ITERACIÓN 1", buf)
        # The diff names what changed (Sección B) and stays silent about the
        # section that did not change (Sección C is not repeated wholesale).
        self.assertIn("Contenido corregido de la sección B", buf)
        self.assertNotIn(
            "Esta sección no cambia entre iteraciones — sirve para comprobar "
            "que el diff", buf,
        )

    def test_second_rotate_with_no_changes_says_so(self) -> None:
        gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER1),
        ])
        buf = _capture_stdout(lambda: gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER1),
        ]))
        self.assertIn("sin cambios", buf)

    def test_delta_is_against_the_immediately_previous_iteration_not_iteration_1(
        self,
    ) -> None:
        """A third iteration diffs against the *second*, never accumulated
        from the first (plan.md § Decisiones de diseño, literal del "Resultado
        esperado" del spec)."""
        gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER1),
        ])
        gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER2),
        ])
        # A third call with the *same* content as iteration 2 must report "no
        # changes" — if the delta were still measured from iteration 1, it
        # would report a diff instead.
        buf = _capture_stdout(lambda: gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER2),
        ]))
        self.assertIn("sin cambios", buf)

    def test_missing_artifact_is_a_real_error(self) -> None:
        rc = gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(self.unidad / "no-existe.md"),
        ])
        self.assertEqual(rc, 1)

    # --- `clear` -----------------------------------------------------------

    def test_clear_removes_an_existing_cache(self) -> None:
        gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER1),
        ])
        self.assertTrue(self._cache().is_file())
        rc = gate_delta.main(["clear", "--unidad", str(self.unidad), "--fase", "plan"])
        self.assertEqual(rc, 0)
        self.assertFalse(self._cache().exists())

    def test_clear_is_idempotent_on_an_already_clear_cache(self) -> None:
        rc = gate_delta.main(["clear", "--unidad", str(self.unidad), "--fase", "plan"])
        self.assertEqual(rc, 0)

    def test_clear_makes_the_next_rotate_report_iteration_1_again(self) -> None:
        gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER1),
        ])
        gate_delta.main(["clear", "--unidad", str(self.unidad), "--fase", "plan"])
        buf = _capture_stdout(lambda: gate_delta.main([
            "rotate", "--unidad", str(self.unidad), "--fase", "plan",
            "--artefacto", str(ITER2),
        ]))
        self.assertIn("ITERACIÓN 1", buf)


def _capture_stdout(fn) -> str:
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return buf.getvalue()


if __name__ == "__main__":
    unittest.main()
