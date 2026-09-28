"""Tests for `guard_generated_paths.py`: an `Edit`/`Write`/`MultiEdit`
about to touch `.claude/agents/sdd-*.md` (or its manifest) or anything under
`.claude/skills/` is denied; every other path -- including the canonical
sources `.agents/agents/**` and `.agents/skills/**` -- passes through.

Same shape as `test_guard_bash_spec_writes.py`: the hook runs as a
**subprocess** inside a throwaway repo root built per test, so its
self-located `REPO_ROOT` is the temporary tree and nothing is written into
the real repo.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# `usage/` is IArk-specific instrumentation (unit 0113, not canonical kit
# andamiaje per its own `__init__.py`) and does not travel with the kit —
# inlined here instead of importing `usage.paths.default_repo_root`: same
# `Path(__file__).resolve().parents[3]` depth (this file also sits 3 levels
# under the repo root, tests/ -> scripts/ -> .spec/ -> repo root).
REAL_REPO = Path(__file__).resolve().parents[3]
REAL_SCRIPTS = REAL_REPO / ".spec" / "scripts"


class GuardRepoMixin:
    """A throwaway repo root with a copy of the hook."""

    def setUp(self) -> None:  # noqa: D401
        self._tmp = Path(tempfile.mkdtemp(prefix="guard-pre-edit-")).resolve()
        self.repo = self._tmp / "repo"
        self.scripts = self.repo / ".spec" / "scripts"
        self.scripts.mkdir(parents=True)
        shutil.copy(
            REAL_SCRIPTS / "guard_generated_paths.py",
            self.scripts / "guard_generated_paths.py",
        )
        self.hook = self.scripts / "guard_generated_paths.py"
        self.usage_dir = self.repo / ".spec" / ".usage"
        self.rejections = self.usage_dir / "guard-rejections-pre-edit.jsonl"
        self.failures = self.usage_dir / "guard-failures.log"

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # -- running the hook --------------------------------------------------------------

    def run_hook_raw(self, raw_stdin: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.hook)],
            input=raw_stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_hook(self, file_path: str, *, tool_name: str = "Edit") -> subprocess.CompletedProcess:
        payload = {
            "session_id": "sesion-de-prueba",
            "cwd": str(self.repo),
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path},
        }
        return self.run_hook_raw(json.dumps(payload))

    # -- assertions --------------------------------------------------------------------

    def decision(self, result: subprocess.CompletedProcess) -> dict | None:
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)

    def deny_reason(self, file_path: str, *, tool_name: str = "Edit") -> str:
        decision = self.decision(self.run_hook(file_path, tool_name=tool_name))
        self.assertIsNotNone(decision, f"no bloqueó: {file_path!r}")
        block = decision["hookSpecificOutput"]
        self.assertEqual(block["hookEventName"], "PreToolUse")
        self.assertEqual(block["permissionDecision"], "deny")
        return block["permissionDecisionReason"]

    def assertNoDecision(self, file_path: str, *, tool_name: str = "Edit") -> None:  # noqa: N802
        result = self.run_hook(file_path, tool_name=tool_name)
        self.assertIsNone(self.decision(result), f"bloqueó y no debía: {file_path!r}")

    def rejection_lines(self) -> list[dict]:
        if not self.rejections.is_file():
            return []
        return [
            json.loads(line)
            for line in self.rejections.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def failure_lines(self) -> list[str]:
        if not self.failures.is_file():
            return []
        return [line for line in self.failures.read_text(encoding="utf-8").splitlines() if line.strip()]


class AgentsMirrorTests(GuardRepoMixin, unittest.TestCase):
    """`.claude/agents/sdd-*.md` and its manifest are denied."""

    def test_role_frontmatter_under_claude_agents_is_denied(self) -> None:
        target = str(self.repo / ".claude" / "agents" / "sdd-critico-profundo.md")
        reason = self.deny_reason(target)
        self.assertIn(".claude/agents/sdd-critico-profundo.md", reason)
        self.assertIn("scripts/materialize_claude_agents.py", reason)

    def test_generated_effort_variant_is_denied(self) -> None:
        target = str(self.repo / ".claude" / "agents" / "sdd-implementador-xhigh.md")
        reason = self.deny_reason(target)
        self.assertIn("sdd-implementador-xhigh.md", reason)
        self.assertIn("scripts/materialize_claude_agents.py", reason)

    def test_manifest_is_denied(self) -> None:
        target = str(self.repo / ".claude" / "agents" / ".claude-agents-manifest.yaml")
        reason = self.deny_reason(target)
        self.assertIn(".claude-agents-manifest.yaml", reason)
        self.assertIn("scripts/materialize_claude_agents.py", reason)

    def test_write_and_multi_edit_are_covered_too(self) -> None:
        target = str(self.repo / ".claude" / "agents" / "sdd-gate.md")
        for tool_name in ("Write", "MultiEdit"):
            reason = self.deny_reason(target, tool_name=tool_name)
            self.assertIn("scripts/materialize_claude_agents.py", reason)

    def test_the_rejection_is_logged(self) -> None:
        target = str(self.repo / ".claude" / "agents" / "sdd-gate.md")
        self.deny_reason(target)
        lines = self.rejection_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["surface"], "agents")
        self.assertEqual(lines[0]["path"], ".claude/agents/sdd-gate.md")


class SkillsMirrorTests(GuardRepoMixin, unittest.TestCase):
    """Anything under `.claude/skills/` is denied."""

    def test_skill_md_under_claude_skills_is_denied(self) -> None:
        target = str(self.repo / ".claude" / "skills" / "sdd-perfil" / "SKILL.md")
        reason = self.deny_reason(target)
        self.assertIn(".claude/skills/sdd-perfil/SKILL.md", reason)
        self.assertIn("scripts/materialize_claude_skills.py", reason)

    def test_skills_manifest_is_denied(self) -> None:
        target = str(self.repo / ".claude" / "skills" / ".claude-skills-manifest.yaml")
        reason = self.deny_reason(target)
        self.assertIn("scripts/materialize_claude_skills.py", reason)

    def test_nested_reference_file_is_denied(self) -> None:
        target = str(self.repo / ".claude" / "skills" / "sdd-gate" / "references" / "rubrica-spec.md")
        reason = self.deny_reason(target)
        self.assertIn("scripts/materialize_claude_skills.py", reason)


class CommandsMirrorTests(GuardRepoMixin, unittest.TestCase):
    """Anything under `.claude/commands/` is denied."""

    def test_command_file_under_claude_commands_is_denied(self) -> None:
        target = str(self.repo / ".claude" / "commands" / "sdd-gate.md")
        reason = self.deny_reason(target)
        self.assertIn(".claude/commands/sdd-gate.md", reason)
        self.assertIn("scripts/materialize_claude_commands.py", reason)

    def test_commands_manifest_is_denied(self) -> None:
        target = str(self.repo / ".claude" / "commands" / ".claude-commands-manifest.yaml")
        reason = self.deny_reason(target)
        self.assertIn("scripts/materialize_claude_commands.py", reason)

    def test_canonical_command_source_passes(self) -> None:
        self.assertNoDecision(str(self.repo / ".agents" / "commands" / "sdd-gate.md"))


class PassThroughTests(GuardRepoMixin, unittest.TestCase):
    """Canonical sources and any other path pass through undecided."""

    def test_canonical_agent_source_passes(self) -> None:
        self.assertNoDecision(str(self.repo / ".agents" / "agents" / "sdd-gate.md"))

    def test_canonical_skill_source_passes(self) -> None:
        self.assertNoDecision(str(self.repo / ".agents" / "skills" / "sdd-perfil" / "SKILL.md"))

    def test_unrelated_path_passes(self) -> None:
        self.assertNoDecision(str(self.repo / "docs" / "notas.md"))

    def test_spec_unit_file_passes(self) -> None:
        self.assertNoDecision(str(self.repo / ".spec" / "units" / "0001-x" / "bitacora.md"))

    def test_a_path_that_merely_contains_the_word_agents_passes(self) -> None:
        self.assertNoDecision(str(self.repo / "orchestrator" / "agents" / "sdd-thing.md"))

    def test_no_rejection_logged_when_nothing_is_denied(self) -> None:
        self.assertNoDecision(str(self.repo / "docs" / "notas.md"))
        self.assertEqual(self.rejection_lines(), [])


class FailOpenTests(GuardRepoMixin, unittest.TestCase):
    """Anything this hook cannot make sense of fails open (no block)."""

    def test_malformed_json_payload_fails_open(self) -> None:
        result = self.run_hook_raw("{not json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")
        self.assertEqual(len(self.failure_lines()), 1)
        self.assertIn("guard_generated_paths", self.failure_lines()[0])

    def test_empty_stdin_fails_open_without_a_failure_entry(self) -> None:
        result = self.run_hook_raw("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")
        self.assertEqual(self.failure_lines(), [])

    def test_payload_that_is_not_an_object_fails_open(self) -> None:
        result = self.run_hook_raw("[1, 2, 3]")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_tool_input_without_file_path_fails_open(self) -> None:
        payload = {
            "session_id": "s",
            "cwd": str(self.repo),
            "tool_name": "Edit",
            "tool_input": {"old_string": "x", "new_string": "y"},
        }
        result = self.run_hook_raw(json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
