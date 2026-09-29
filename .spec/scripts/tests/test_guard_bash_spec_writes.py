"""Tests for `guard_bash_spec_writes.py` (unit 0114: CA-19, CA-20, CA-21,
CA-22b, CA-22c, CA-23, D-16).

Same shape as the post-write suite: the hook runs as a **subprocess** inside a
throwaway repo root built per test, so its self-located `REPO_ROOT` is the
temporary tree and nothing is written into the real repo. The pattern fixture
travels with it, because it is the hook's only file read.

CA-22a (no network) lives in `test_scripts_naming_and_strict_mode.py` by `ast`
over the imports of both hooks, as `plan.md` fixes it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import guard_bash_spec_writes as guard

# `usage/` is IArk-specific instrumentation (unit 0113, not canonical kit
# andamiaje per its own `__init__.py`) and does not travel with the kit —
# inlined here instead of importing `usage.paths.default_repo_root`: same
# `Path(__file__).resolve().parents[3]` depth (this file also sits 3 levels
# under the repo root, tests/ -> scripts/ -> .spec/ -> repo root).
REAL_REPO = Path(__file__).resolve().parents[3]
REAL_SCRIPTS = REAL_REPO / ".spec" / "scripts"
REAL_PATTERNS = REAL_REPO / ".spec" / "_fixtures" / "patrones-prohibidos.txt"

#: Budget of CA-22b. If the worst case exceeds it the hook does not ship.
HOOK_TIME_BUDGET_SECONDS = 2.0

#: Size of the worst-case command of CA-22b.
WORST_CASE_BYTES = 64 * 1024


class GuardRepoMixin:
    """A throwaway repo root with the hook and the pattern fixture."""

    def setUp(self) -> None:  # noqa: D401
        self._tmp = Path(tempfile.mkdtemp(prefix="guard-pre-bash-")).resolve()
        self.repo = self._tmp / "repo"
        self.scripts = self.repo / ".spec" / "scripts"
        self.scripts.mkdir(parents=True)
        shutil.copy(REAL_SCRIPTS / "guard_bash_spec_writes.py", self.scripts / "guard_bash_spec_writes.py")
        self.hook = self.scripts / "guard_bash_spec_writes.py"
        self.patterns = self.repo / ".spec" / "_fixtures" / "patrones-prohibidos.txt"
        self.patterns.parent.mkdir(parents=True)
        shutil.copy(REAL_PATTERNS, self.patterns)
        self.usage_dir = self.repo / ".spec" / ".usage"
        self.rejections = self.usage_dir / "guard-rejections-pre-bash.jsonl"
        self.post_write_rejections = self.usage_dir / "guard-rejections-post-write.jsonl"
        self.failures = self.usage_dir / "guard-failures.log"
        # One existing file under `.spec/` in a plain unit, plus its twin inside
        # the pilot's test unit, so every rule has a target either way.
        self.unit_file = self._touch(".spec/units/0000-unidad/bitacora.md")
        self.pilot_file = self._touch(".spec/units/9109-prueba-supervisado/bitacora.md")
        self.index_file = self._touch(".spec/units/_mandatos-supervisado.md")
        self.outside_file = self._touch("docs/notas.md")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _touch(self, rel: str) -> Path:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("contenido\n", encoding="utf-8")
        return path

    # -- running the hook --------------------------------------------------------------

    def run_hook(self, command: str, *, exempt_tree: str | None = None) -> subprocess.CompletedProcess:
        payload = {
            "session_id": "sesion-de-prueba",
            "cwd": str(self.repo),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        env = dict(os.environ)
        env.pop("SDD_GUARD_EXEMPT_TREE", None)
        if exempt_tree is not None:
            env["SDD_GUARD_EXEMPT_TREE"] = exempt_tree
        return subprocess.run(
            [sys.executable, str(self.hook)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    # -- assertions --------------------------------------------------------------------

    def decision(self, result: subprocess.CompletedProcess) -> dict | None:
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)

    def deny_reason(self, command: str, *, exempt_tree: str | None = None) -> str:
        decision = self.decision(self.run_hook(command, exempt_tree=exempt_tree))
        self.assertIsNotNone(decision, f"no bloqueó: {command!r}")
        block = decision["hookSpecificOutput"]
        self.assertEqual(block["hookEventName"], "PreToolUse")
        self.assertEqual(block["permissionDecision"], "deny")
        return block["permissionDecisionReason"]

    def rejection_lines(self) -> list[dict]:
        if not self.rejections.is_file():
            return []
        return [json.loads(line) for line in self.rejections.read_text(encoding="utf-8").splitlines() if line.strip()]

    def failure_lines(self) -> list[str]:
        if not self.failures.is_file():
            return []
        return [line for line in self.failures.read_text(encoding="utf-8").splitlines() if line.strip()]

    def assertNoDecision(self, command: str, *, exempt_tree: str | None = None) -> None:  # noqa: N802 — mimics unittest's own assert* naming convention
        result = self.run_hook(command, exempt_tree=exempt_tree)
        self.assertIsNone(self.decision(result), f"bloqueó y no debía: {command!r}")
        self.assertEqual(self.rejection_lines(), [])


class SedInPlaceTests(GuardRepoMixin, unittest.TestCase):
    """CA-19, rule 1."""

    def test_sed_in_place_under_spec_is_denied(self) -> None:
        reason = self.deny_reason("sed -i '' s/a/b/ .spec/units/0000-unidad/bitacora.md")
        self.assertTrue(reason.startswith("regla 1: "), reason)
        self.assertIn(".spec/units/0000-unidad/bitacora.md", reason)
        self.assertIn("herramienta de edición del harness", reason)
        self.assertIn("script versionado de .spec/scripts/", reason)

    def test_the_message_neither_demands_nor_forbids_a_stop(self) -> None:
        reason = self.deny_reason("sed -i '' s/a/b/ .spec/units/0000-unidad/bitacora.md")
        self.assertNotIn("parada", reason.lower())
        self.assertNotIn("condición 9", reason.lower())
        self.assertNotIn("condicion 9", reason.lower())

    def test_sed_in_place_outside_spec_is_not_denied(self) -> None:
        self.assertNoDecision("sed -i '' s/a/b/ docs/notas.md")

    def test_sed_without_in_place_is_not_denied(self) -> None:
        self.assertNoDecision("sed s/a/b/ .spec/units/0000-unidad/bitacora.md")

    def test_sed_in_a_compound_command_is_denied(self) -> None:
        reason = self.deny_reason("cd repo && sed -i.bak s/a/b/ .spec/units/0000-unidad/bitacora.md")
        self.assertTrue(reason.startswith("regla 1: "), reason)

    def test_the_file_is_never_touched_by_the_hook(self) -> None:
        before = self.unit_file.read_bytes()
        self.deny_reason("sed -i '' s/a/b/ .spec/units/0000-unidad/bitacora.md")
        self.assertEqual(self.unit_file.read_bytes(), before)


class TruncatingRedirectTests(GuardRepoMixin, unittest.TestCase):
    """CA-20, rule 2."""

    def test_truncating_redirect_over_an_existing_file_is_denied(self) -> None:
        reason = self.deny_reason("echo hola > .spec/units/0000-unidad/bitacora.md")
        self.assertTrue(reason.startswith("regla 2: "), reason)
        self.assertIn(".spec/units/0000-unidad/bitacora.md", reason)

    def test_truncating_redirect_over_a_missing_file_is_not_denied(self) -> None:
        self.assertNoDecision("echo hola > .spec/units/0000-unidad/nuevo.md")

    def test_append_over_an_existing_file_is_not_denied(self) -> None:
        self.assertNoDecision("echo hola >> .spec/units/0000-unidad/bitacora.md")

    def test_redirect_outside_spec_is_not_denied(self) -> None:
        self.assertNoDecision("echo hola > docs/notas.md")

    def test_redirect_written_without_a_space_is_denied(self) -> None:
        reason = self.deny_reason("echo hola >.spec/units/0000-unidad/bitacora.md")
        self.assertTrue(reason.startswith("regla 2: "), reason)


class InlineInterpreterTests(GuardRepoMixin, unittest.TestCase):
    """CA-21, rule 3, and S-6's boundary: a versioned script is not inline code."""

    def test_dash_c_with_a_spec_path_is_denied(self) -> None:
        reason = self.deny_reason(
            "python3 -c 'open(\".spec/units/0000-unidad/bitacora.md\", \"w\").write(\"x\")'"
        )
        self.assertTrue(reason.startswith("regla 3: "), reason)

    def test_heredoc_with_a_spec_path_is_denied(self) -> None:
        command = (
            "python3 <<'EOF'\n"
            "open('.spec/units/0000-unidad/bitacora.md', 'w').write('x')\n"
            "EOF\n"
        )
        reason = self.deny_reason(command)
        self.assertTrue(reason.startswith("regla 3: "), reason)

    def test_an_interpreter_outside_the_closed_list_is_not_denied(self) -> None:
        """Documented outcome of CA-21, not a hole: widening the list is editing
        the fixture, never the hook."""
        self.assertNoDecision("awk '{ print \".spec/units/0000-unidad/bitacora.md\" }' /dev/null")

    def test_invoking_a_versioned_script_with_spec_arguments_is_not_denied(self) -> None:
        self.assertNoDecision(
            "python3 .spec/scripts/validate_mandate.py --unidad .spec/units/0000-unidad"
        )

    def test_invoking_a_versioned_shell_script_is_not_denied(self) -> None:
        self.assertNoDecision("bash .spec/scripts/supervised-test.sh capturar-unidad .spec/units/0000-unidad /tmp/x")

    def test_dash_c_without_any_spec_path_is_not_denied(self) -> None:
        self.assertNoDecision("python3 -c 'print(1)'")


class InterpreterListTests(unittest.TestCase):
    """CA-21: the closed list lives in the versioned fixture, not in the hook."""

    def test_the_list_is_exactly_what_the_fixture_declares(self) -> None:
        declared = tuple(
            line.split("|", 1)[1].strip()
            for line in REAL_PATTERNS.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("interprete|")
        )
        self.assertEqual(guard.load_interpreters(), declared)

    def test_the_seven_interpreters_of_the_criterion_are_declared(self) -> None:
        self.assertEqual(
            set(guard.load_interpreters()),
            {"python3", "python", "bash", "sh", "zsh", "node", "perl"},
        )
        self.assertNotIn("awk", guard.load_interpreters())


class PilotExemptionTests(GuardRepoMixin, unittest.TestCase):
    """D-16: the pilot's conductor has Bash as its only route inside `claude -p`."""

    def test_a_command_confined_to_the_pilot_prefix_is_not_denied(self) -> None:
        self.assertNoDecision("sed -i '' s/a/b/ .spec/units/9109-prueba-supervisado/bitacora.md")

    def test_the_same_command_on_another_unit_is_denied(self) -> None:
        self.deny_reason("sed -i '' s/a/b/ .spec/units/0000-unidad/bitacora.md")

    def test_the_index_is_denied_even_inside_the_pilot(self) -> None:
        """El índice de mandatos (`_mandatos-supervisado.md`, sustituto post-U-0009
        del archivo histórico que vivía bajo `.spec/units/`) está fuera de
        `$TEST_UNIT`: una edición directa del archivo se rechaza por la regla
        general "todo `.spec/units/*` distinto a `$TEST_UNIT` requiere
        aprobación" — no hay un script específico que lo anexe, el archivo se
        mantiene por la skill `sdd-supervisado`."""
        reason = self.deny_reason(
            "echo linea > .spec/units/_mandatos-supervisado.md",
            exempt_tree=".spec/units/9109-prueba-supervisado",
        )
        self.assertIn(".spec/units/_mandatos-supervisado.md", reason)

    def test_one_exempt_path_and_one_outside_still_denies(self) -> None:
        reason = self.deny_reason(
            "sed -i '' s/a/b/ .spec/units/9109-prueba-supervisado/bitacora.md "
            ".spec/units/0000-unidad/bitacora.md"
        )
        self.assertIn(".spec/units/0000-unidad/bitacora.md", reason)

    def test_exempt_tree_variable_covers_a_test_unit_outside_the_prefix(self) -> None:
        self._touch(".spec/units/9500-otra/bitacora.md")
        self.assertNoDecision(
            "sed -i '' s/a/b/ .spec/units/9500-otra/bitacora.md",
            exempt_tree=".spec/units/9500-otra",
        )
        self.assertEqual(self.failure_lines(), [])

    def test_empty_exempt_tree_still_denies_and_leaves_a_trace(self) -> None:
        self.deny_reason("sed -i '' s/a/b/ .spec/units/0000-unidad/bitacora.md", exempt_tree="")
        self.assertTrue(any("exempt-tree-ignored" in line for line in self.failure_lines()))

    def test_exempt_tree_outside_units_still_denies_and_leaves_a_trace(self) -> None:
        self.deny_reason("sed -i '' s/a/b/ .spec/units/0000-unidad/bitacora.md", exempt_tree=".spec")
        self.assertTrue(any("exempt-tree-ignored" in line for line in self.failure_lines()))


class RejectionsLogTests(GuardRepoMixin, unittest.TestCase):
    """CA-23: two separate logs; this one feeds no indicator."""

    def test_a_denial_appends_one_line_with_date_session_tool_path_and_rule(self) -> None:
        self.deny_reason("sed -i '' s/a/b/ .spec/units/0000-unidad/bitacora.md")
        lines = self.rejection_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["tool"], "Bash")
        self.assertEqual(lines[0]["path"], ".spec/units/0000-unidad/bitacora.md")
        self.assertEqual(lines[0]["rule"], "regla 1")
        self.assertEqual(lines[0]["session_id"], "sesion-de-prueba")
        self.assertTrue(lines[0]["ts"].endswith("Z"))

    def test_a_denial_never_touches_the_post_write_log_of_k6(self) -> None:
        self.deny_reason("sed -i '' s/a/b/ .spec/units/0000-unidad/bitacora.md")
        self.assertFalse(self.post_write_rejections.exists())

    def test_rejections_log_is_the_literal_expected_path(self) -> None:
        self.assertEqual(
            guard.REJECTIONS_LOG,
            REAL_REPO / ".spec/.usage/guard-rejections-pre-bash.jsonl",
        )


class FailOpenTests(GuardRepoMixin, unittest.TestCase):
    """CA-22c: a failure of the hook's own never blocks the tool."""

    def test_an_unreadable_pattern_fixture_logs_and_does_not_deny(self) -> None:
        self.patterns.chmod(0o000)
        try:
            if os.access(self.patterns, os.R_OK):  # running as root
                self.skipTest("el proceso puede leer un archivo con modo 000 (¿root?)")
            self.assertNoDecision("sed -i '' s/a/b/ .spec/units/0000-unidad/bitacora.md")
            self.assertTrue(self.failure_lines())
            self.assertFalse(self.post_write_rejections.exists())
        finally:
            self.patterns.chmod(0o644)

    def test_a_payload_that_is_not_json_does_not_deny(self) -> None:
        env = dict(os.environ)
        env.pop("SDD_GUARD_EXEMPT_TREE", None)
        result = subprocess.run(
            [sys.executable, str(self.hook)],
            input="esto no es JSON",
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")
        self.assertTrue(self.failure_lines())

    def test_an_unbalanced_quote_degrades_the_tokenizer_instead_of_failing_open(self) -> None:
        """A lone apostrophe is common enough that a plain fail-open would be a
        standing hole: the rules stay active on a whitespace split and the
        degradation leaves a trace."""
        self.deny_reason("echo it's fine > .spec/units/0000-unidad/bitacora.md")
        self.assertTrue(any("tokenize-degraded" in line for line in self.failure_lines()))


class WorstCaseBudgetTests(GuardRepoMixin, unittest.TestCase):
    """CA-22b: a 64 KiB command concatenating every pattern of the fixture."""

    def _worst_case_command(self) -> str:
        values = [
            line.split("|", 1)[1].strip()
            for line in REAL_PATTERNS.read_text(encoding="utf-8").splitlines()
            if "|" in line and not line.strip().startswith("#")
        ]
        self.assertTrue(values)
        # A heredoc so the full-text scan of rule 3 runs over every byte -- the
        # most expensive path the hook has.
        body: list[str] = []
        size = 0
        while size < WORST_CASE_BYTES:
            for value in values:
                body.append(value)
                size += len(value) + 1
        body.append(".spec/units/0000-unidad/bitacora.md")
        command = "python3 <<'EOF'\n" + " ".join(body) + "\nEOF\n"
        self.assertGreaterEqual(len(command.encode("utf-8")), WORST_CASE_BYTES)
        return command

    def test_the_worst_case_command_stays_within_the_budget(self) -> None:
        command = self._worst_case_command()
        start = time.monotonic()
        result = self.run_hook(command)
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(self.decision(result), "el caso peor debía bloquear")
        self.assertLess(
            elapsed,
            HOOK_TIME_BUDGET_SECONDS,
            f"comando de {len(command.encode('utf-8'))} B: {elapsed:.3f}s",
        )


if __name__ == "__main__":
    unittest.main()
