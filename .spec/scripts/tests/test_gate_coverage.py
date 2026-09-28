"""Tests for `gate_coverage.py` (unit 0116, G3 — CA-12).

`lenses` is exercised against the real rubrics under
`.agents/skills/sdd-gate/references/rubrica-<fase>.md` (the source of truth
this unit's plan requires it to read, never hardcode) — not a fixture copy,
because a fixture copy would defeat the point: this suite must notice if a
rubric's own table stops matching what `lenses` expects.

`decisions` is exercised against this very unit's `spec.md`, whose "##
Preguntas abiertas" already uses the `**resuelto**`/`**abierta**` convention
(3 resueltas, 1 abierta — `RS-2`), so there is no need for a synthetic
fixture either.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import gate_coverage  # noqa: E402

REPO_ROOT = SCRIPTS.parent.parent
RUBRICS_DIR = REPO_ROOT / ".agents" / "skills" / "sdd-gate" / "references"
UNIT_SPEC = (
    REPO_ROOT / ".spec" / "_fixtures" / "gate-decisions" / "artefacto.md"
)


def _capture_stdout(fn) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn()
    return buf.getvalue(), rc


class ParseLensesTests(unittest.TestCase):
    """Direct unit tests of the table parser against every real rubric —
    catches a rubric whose "## Lentes del panel" table stops having the
    three required columns."""

    def test_every_rubric_has_a_recognizable_lenses_table(self) -> None:
        for fase in ("spec", "plan", "tasks", "codigo"):
            text = (RUBRICS_DIR / f"rubrica-{fase}.md").read_text(encoding="utf-8")
            with self.subTest(fase=fase):
                lenses = gate_coverage.parse_lenses(text)
                self.assertIsNotNone(lenses)
                self.assertGreater(len(lenses), 0)

    def test_obligatorio_lenses_are_active_in_every_tier(self) -> None:
        text = (RUBRICS_DIR / "rubrica-spec.md").read_text(encoding="utf-8")
        lenses = gate_coverage.parse_lenses(text)
        obligatorios = [lens for lens in lenses if "obligatorio" in lens["tipo"].lower()]
        self.assertTrue(obligatorios)
        for lens in obligatorios:
            for tier in ("bajo", "medio", "alto"):
                with self.subTest(lente=lens["lente"], tier=tier):
                    self.assertTrue(gate_coverage.is_active(lens, tier))

    def test_opcional_lense_only_active_in_the_tier_it_names(self) -> None:
        text = (RUBRICS_DIR / "rubrica-plan.md").read_text(encoding="utf-8")
        lenses = gate_coverage.parse_lenses(text)
        # L2 — Simplicidad / YAGNI is `opcional`, "Se ejecuta en" = `alto`.
        l2 = next(lens for lens in lenses if "Simplicidad" in lens["lente"])
        self.assertFalse(gate_coverage.is_active(l2, "bajo"))
        self.assertTrue(gate_coverage.is_active(l2, "alto"))


class CmdLensesTests(unittest.TestCase):
    def test_lenses_command_reports_active_over_total(self) -> None:
        buf, rc = _capture_stdout(lambda: gate_coverage.main([
            "lenses", "--fase", "spec", "--tier", "bajo",
            "--rubricas-dir", str(RUBRICS_DIR),
        ]))
        self.assertEqual(rc, 0)
        self.assertIn("Lentes activos:", buf)
        self.assertIn("fase spec, tier bajo", buf)

    def test_lenses_command_alto_activates_more_than_bajo(self) -> None:
        buf_bajo, _ = _capture_stdout(lambda: gate_coverage.main([
            "lenses", "--fase", "spec", "--tier", "bajo",
            "--rubricas-dir", str(RUBRICS_DIR),
        ]))
        buf_alto, _ = _capture_stdout(lambda: gate_coverage.main([
            "lenses", "--fase", "spec", "--tier", "alto",
            "--rubricas-dir", str(RUBRICS_DIR),
        ]))

        def active_count(buf: str) -> int:
            first_line = buf.splitlines()[0]
            return int(first_line.split("Lentes activos: ")[1].split("/")[0])

        self.assertGreaterEqual(active_count(buf_alto), active_count(buf_bajo))

    def test_missing_rubric_is_a_real_error(self) -> None:
        rc = gate_coverage.main([
            "lenses", "--fase", "spec", "--tier", "bajo",
            "--rubricas-dir", "/no/existe/nunca",
        ])
        self.assertEqual(rc, 1)

    def test_default_rubricas_dir_resolves_to_the_repo_references(self) -> None:
        self.assertEqual(gate_coverage.DEFAULT_RUBRICS_DIR.resolve(), RUBRICS_DIR.resolve())

    def test_perfil_is_accepted_and_reported_but_never_changes_the_count(self) -> None:
        """CA-12: `--perfil` is accepted and echoed back, but the mandatory
        lens set depends only on `--fase`/`--tier` — never on `--perfil`."""
        buf_no_perfil, rc_no_perfil = _capture_stdout(lambda: gate_coverage.main([
            "lenses", "--fase", "codigo", "--tier", "alto",
            "--rubricas-dir", str(RUBRICS_DIR),
        ]))
        buf_perfil, rc_perfil = _capture_stdout(lambda: gate_coverage.main([
            "lenses", "--fase", "codigo", "--tier", "alto", "--perfil", "profundo",
            "--rubricas-dir", str(RUBRICS_DIR),
        ]))
        self.assertEqual(rc_no_perfil, 0)
        self.assertEqual(rc_perfil, 0)
        self.assertIn("perfil profundo", buf_perfil)
        self.assertNotIn("perfil", buf_no_perfil)

        def active_over_total(buf: str) -> str:
            return buf.splitlines()[0].split("Lentes activos: ")[1].split(" (")[0]

        self.assertEqual(active_over_total(buf_perfil), active_over_total(buf_no_perfil))

    def test_perfil_rejects_an_unknown_value(self) -> None:
        with self.assertRaises(SystemExit):
            gate_coverage.main([
                "lenses", "--fase", "codigo", "--tier", "alto", "--perfil", "no-existe",
                "--rubricas-dir", str(RUBRICS_DIR),
            ])


class CmdDecisionsTests(unittest.TestCase):
    def test_spec_phase_counts_resolved_over_total(self) -> None:
        buf, rc = _capture_stdout(lambda: gate_coverage.main([
            "decisions", "--fase", "spec", "--artefacto", str(UNIT_SPEC),
        ]))
        self.assertEqual(rc, 0)
        self.assertIn("3/4", buf)

    def test_non_spec_phase_is_na_never_a_fabricated_zero(self) -> None:
        for fase in ("plan", "tasks", "codigo"):
            with self.subTest(fase=fase):
                buf, rc = _capture_stdout(lambda fase=fase: gate_coverage.main([
                    "decisions", "--fase", fase,
                ]))
                self.assertEqual(rc, 0)
                self.assertIn("n/a", buf)
                self.assertNotIn("0/", buf)

    def test_spec_phase_without_artefacto_is_an_error(self) -> None:
        rc = gate_coverage.main(["decisions", "--fase", "spec"])
        self.assertEqual(rc, 1)

    def test_missing_artefacto_is_a_real_error(self) -> None:
        rc = gate_coverage.main([
            "decisions", "--fase", "spec", "--artefacto", "/no/existe/nunca.md",
        ])
        self.assertEqual(rc, 1)

    def test_marker_search_covers_bullet_continuation_lines(self) -> None:
        """The `**resuelto**` marker sits on the line *after* the bullet's
        opening dash in this unit's own `spec.md` — a parser that only looked
        at the first line of each bullet would undercount (regression guard
        for that exact bug)."""
        text = (
            "## Preguntas abiertas\n\n"
            "- Pregunta uno —\n"
            "  **resuelto (conservador)**: detalle.\n"
            "- Pregunta dos — **abierta, no resuelta aquí**: detalle.\n"
        )
        resolved, total = gate_coverage.parse_decisions(text)
        self.assertEqual((resolved, total), (1, 2))


if __name__ == "__main__":
    unittest.main()
