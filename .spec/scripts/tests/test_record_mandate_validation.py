"""Tests for `record_mandate_validation.py` (unit 0114, G2 — CA-12, CA-11a/b/c).

Depends on `codes_from_output()` of `_common.py` (G1, T2): this script imports
it directly (no parsing of its own, `pri-gob-fuente-verdad-unica`), so this
suite only closes once G1 is integrated alongside G2 (plan.md § Grupos de
tareas paralelizables: "G2 codifica contra [la firma] fijada en este plan
... su test ... se da por cerrado cuando G1 integre"). The fixed signature it
codes against: `codes_from_output(stdout: str) -> list[str]`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import record_mandate_validation as rmv  # noqa: E402

REPO_ROOT = SCRIPTS.parent.parent
EXPECTED_FAIL_CODE = "unidad-supervisado-sin-mandato"

# Inline seed (no dependency on any fixture tree): a unit with `modo:
# supervisado` and no `mandato:` field — the stable invalid case CA-04 needs,
# whose single code is `unidad-supervisado-sin-mandato`.
INVALID_UNIT_ESTADO = """\
id: "0000-fixture-unidad-todo-mal"
titulo: "Fixture unidad-todo-mal (sin mandato, intencionalmente inválido)"
modo: supervisado
fase: spec
estado: en-curso
"""


class RecordMandateValidationTests(unittest.TestCase):
    """A unit whose `_estado.yaml` declares `modo: supervisado` without a
    `mandato:` field already fails `validate_mandate.py --unidad` with the
    single code `unidad-supervisado-sin-mandato` — exactly the case CA-12
    needs, an entry whose `exit`/`codigos` are non-trivial and whose re-run
    still reports no *new* code."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="record-mandate-validation-")
        self.unit_dir = Path(self._tmp) / "unidad"
        self.unit_dir.mkdir(parents=True)
        (self.unit_dir / "_estado.yaml").write_text(INVALID_UNIT_ESTADO, encoding="utf-8")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _state_text(self) -> str:
        return (self.unit_dir / "_estado.yaml").read_text(encoding="utf-8")

    # --- CA-11a — dry-run byte-identity -----------------------------------

    def test_dry_run_is_byte_identical_to_the_line_a_real_run_appends(self) -> None:
        before = self._state_text()
        dry = subprocess.run(
            [sys.executable, "record_mandate_validation.py",
             "--unit", str(self.unit_dir), "--phase", "implement", "--dry-run"],
            cwd=SCRIPTS, capture_output=True, text=True, check=False,
        )
        self.assertEqual(dry.returncode, rmv.EXIT_OK)
        # Dry-run does not write.
        self.assertEqual(self._state_text(), before)

        real = subprocess.run(
            [sys.executable, "record_mandate_validation.py",
             "--unit", str(self.unit_dir), "--phase", "implement"],
            cwd=SCRIPTS, capture_output=True, text=True, check=False,
        )
        self.assertEqual(real.returncode, rmv.EXIT_OK)
        after = self._state_text()
        self.assertNotEqual(after, before)

        added_lines = [
            line for line in after.splitlines()
            if line not in before.splitlines()
        ]
        # The fixture's `validaciones_mandato: []` is flow style: appending
        # the first entry also expands it to block style, so two lines are
        # new (the `validaciones_mandato:` header plus the entry) even
        # though only the entry line is the "appended" one CA-11a cares
        # about. Byte-identity is about that one line, not the total diff
        # size — assert the entry line is present and is the only *bullet*
        # line added (the header is a one-time, deterministic side effect of
        # the flow→block conversion, not a second appended entry).
        entry_lines = [line for line in added_lines if line.lstrip().startswith("- {")]
        self.assertEqual(len(entry_lines), 1)
        # Byte-identity: dry-run stdout IS the line a real run appends.
        # Dry and real are separate processes: if the wall clock crosses a
        # second boundary between them, only `fecha` differs. Normalize
        # that field for the dry↔real compare; real↔file stays strict
        # (same process emits both).
        def _norm(s: str) -> str:
            return re.sub(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", "<fecha>", s
            )

        self.assertEqual(_norm(dry.stdout.rstrip("\n")), _norm(entry_lines[0]))
        self.assertEqual(real.stdout.rstrip("\n"), entry_lines[0])

    # --- CA-11b — real run, preconditions met ------------------------------

    def test_real_run_appends_the_documented_entry_form_and_exits_zero(self) -> None:
        rc = rmv.main(["--unit", str(self.unit_dir), "--phase", "implement"])
        self.assertEqual(rc, rmv.EXIT_OK)
        text = self._state_text()
        self.assertIn("validaciones_mandato:\n", text)
        self.assertIn(
            f"fase: implement, exit: 1, codigos: [{EXPECTED_FAIL_CODE}]",
            text,
        )

    def test_second_run_with_the_same_line_is_not_an_error_appends_again(self) -> None:
        # Unlike the changelog ritual (CA-13), this one is a log: re-running it
        # is not "the same line" in the idempotent sense — each run is its own
        # dated entry (CA-11b only promises exit 0 and the documented effect).
        rc1 = rmv.main(["--unit", str(self.unit_dir), "--phase", "implement"])
        rc2 = rmv.main(["--unit", str(self.unit_dir), "--phase", "retoma"])
        self.assertEqual((rc1, rc2), (rmv.EXIT_OK, rmv.EXIT_OK))
        text = self._state_text()
        self.assertEqual(text.count("- {fecha:"), 2)

    # --- CA-11c — precondition unmet ---------------------------------------

    def test_nonexistent_unit_exits_1_and_writes_nothing(self) -> None:
        missing = Path(self._tmp) / "no-such-unit"
        rc = rmv.main(["--unit", str(missing), "--phase", "implement"])
        self.assertEqual(rc, rmv.EXIT_NOT_FOUND)
        self.assertFalse(missing.exists())

    def test_nonexistent_mandate_exits_1_and_writes_nothing(self) -> None:
        before = self._state_text()
        rc = rmv.main([
            "--unit", str(self.unit_dir), "--phase", "lanzamiento",
            "--mandate", "no-existe-el-plan-0114",
        ])
        self.assertEqual(rc, rmv.EXIT_NOT_FOUND)
        self.assertEqual(self._state_text(), before)

    # --- CA-12 — entry form + re-validation reports no new code ------------

    def test_appended_state_still_validates_with_no_new_code(self) -> None:
        before_codes = self._validator_codes()
        rmv.main(["--unit", str(self.unit_dir), "--phase", "implement"])
        after_codes = self._validator_codes()
        self.assertEqual(before_codes, after_codes)

    def _validator_codes(self) -> set[str]:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "validate_mandate.py"),
             "--unidad", str(self.unit_dir)],
            capture_output=True, text=True, check=False,
        )
        for line in proc.stdout.splitlines():
            if line.startswith("códigos: "):
                return set(line[len("códigos: "):].split())
        return set()

    # --- --mandate (plan) form ----------------------------------------------

    @unittest.skip(
        "Needs a real, schema-valid `.spec/planes/plan-ejemplo/plan.md` (or "
        "legacy `plan-maestro.md`) that `validate_mandate.py --plan` accepts "
        "with zero codes — RI2 only renamed the literal fixture name (was "
        "`kit-desarrollo-sistecredito`, a real plan of the source repo since "
        "retired there too), it did not fabricate a schema-valid plan mandate "
        "to back it. Crafting one requires validate_mandate.py's full plan-"
        "mandate schema, out of this extraction's scope."
    )
    def test_mandate_flag_validates_the_plan_not_the_unit(self) -> None:
        rc = rmv.main([
            "--unit", str(self.unit_dir), "--phase", "lanzamiento",
            "--mandate", "plan-ejemplo", "--dry-run",
        ])
        self.assertEqual(rc, rmv.EXIT_OK)


class Form2MultiLineAppendTests(unittest.TestCase):
    """Regression: after `normalize_canonical` expands flow entries under
    `validaciones_mandato` to multi-line block mappings, Form 2 must append
    after the last body line of the list, not after the first `  - ` line
    (gate finding alta-3 / record_mandate_validation.py)."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="record-mandate-form2-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.state = Path(self._tmp) / "_estado.yaml"

    def test_appends_after_multiline_block_entry_without_corrupting_yaml(self) -> None:
        import yaml

        self.state.write_text(
            "id: x\n"
            "validaciones_mandato:\n"
            '  - fecha: "2026-01-01T00:00:00Z"\n'
            "    fase: spec\n"
            "    exit: 0\n"
            "    codigos: []\n"
            "\n"
            "siguiente: clave\n",
            encoding="utf-8",
        )
        new_line = (
            '  - {fecha: "2026-01-02T00:00:00Z", fase: plan, '
            "exit: 1, codigos: [x]}"
        )
        rmv.append_entry(self.state, new_line)
        text = self.state.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        self.assertEqual(data["siguiente"], "clave")
        self.assertEqual(len(data["validaciones_mandato"]), 2)
        self.assertIn(new_line, text)
        # New entry sits after the whole first mapping, not between its keys.
        first_entry_end = text.index("codigos: []")
        new_entry_at = text.index(new_line)
        self.assertGreater(new_entry_at, first_entry_end)

    def test_still_appends_to_single_line_flow_entries(self) -> None:
        import yaml

        self.state.write_text(
            "id: x\n"
            "validaciones_mandato:\n"
            '  - {fecha: "2026-01-01T00:00:00Z", fase: spec, exit: 0, codigos: []}\n'
            "siguiente: clave\n",
            encoding="utf-8",
        )
        new_line = '  - {fecha: "2026-01-02T00:00:00Z", fase: plan, exit: 1, codigos: []}'
        rmv.append_entry(self.state, new_line)
        data = yaml.safe_load(self.state.read_text(encoding="utf-8"))
        self.assertEqual(len(data["validaciones_mandato"]), 2)
        self.assertEqual(data["siguiente"], "clave")


if __name__ == "__main__":
    unittest.main()
