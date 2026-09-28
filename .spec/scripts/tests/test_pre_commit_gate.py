"""Tests for `pre-commit-gate.sh` (CA-31): a commit touching a generated SDD
surface runs the four `--check` commands and is blocked if any of them
fails; a commit that touches none of those surfaces always passes without
running anything; `--install` links the script as `.git/hooks/pre-commit`,
resolved via `git rev-parse --git-path hooks` so it also works from a linked
worktree.

Same shape as `test_pre_push_gate.py`: a throwaway git repo per test, with
tiny stub scripts standing in for `materialize_claude_skills.py`,
`materialize_claude_agents.py`, `validate_protocol_drift.py` and
`generate_minor_changes_index.py` so the test controls PASS/BLOCK
deterministically without depending on the real repo's state.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_SRC = REPO_ROOT / ".spec" / "scripts" / "pre-commit-gate.sh"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


class PreCommitGateTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory(prefix="pre-commit-gate-")
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Test")
        (self.repo / "README.md").write_text("x\n")
        _git(self.repo, "add", "README.md")
        _git(self.repo, "commit", "-q", "-m", "base")

        # Copy the real gate script into the throwaway repo so `--install`'s
        # `BASH_SOURCE`-derived symlink target is self-contained.
        self.script = self.repo / ".spec" / "scripts" / "pre-commit-gate.sh"
        self.script.parent.mkdir(parents=True, exist_ok=True)
        self.script.write_text(SCRIPT_SRC.read_text(encoding="utf-8"), encoding="utf-8")
        self.script.chmod(0o755)

        self._write_stub("scripts/materialize_claude_skills.py", 0)
        self._write_stub("scripts/materialize_claude_agents.py", 0)
        self._write_stub(".spec/scripts/validate_protocol_drift.py", 0)
        self._write_stub(".spec/scripts/generate_minor_changes_index.py", 0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_stub(self, rel: str, exit_code: int) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/usr/bin/env python3\nimport sys\nsys.exit({exit_code})\n")

    def _run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(self.script), *args],
            cwd=cwd or self.repo,
            capture_output=True,
            text=True,
        )

    def test_no_generated_surface_touched_passes_without_running_checks(self) -> None:
        # A stub that would fail if actually invoked, to prove it never runs.
        self._write_stub("scripts/materialize_claude_skills.py", 1)
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "notes.md").write_text("hola\n")
        _git(self.repo, "add", "docs/notes.md")

        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_generated_surface_touched_and_all_checks_pass(self) -> None:
        (self.repo / ".claude" / "agents").mkdir(parents=True)
        (self.repo / ".claude" / "agents" / "sdd-x.md").write_text("---\nmodel: sonnet\n---\n")
        _git(self.repo, "add", ".claude/agents/sdd-x.md")

        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_generated_surface_touched_and_a_check_fails_blocks(self) -> None:
        (self.repo / ".claude" / "skills").mkdir(parents=True)
        (self.repo / ".claude" / "skills" / "x.md").write_text("x\n")
        _git(self.repo, "add", ".claude/skills/x.md")
        self._write_stub("scripts/materialize_claude_skills.py", 1)

        result = self._run("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("BLOCK", result.stderr)

    def test_perfiles_yaml_touched_runs_the_checks(self) -> None:
        (self.repo / ".spec").mkdir(exist_ok=True)
        (self.repo / ".spec" / "perfiles.yaml").write_text("default: estandar\n")
        _git(self.repo, "add", ".spec/perfiles.yaml")
        self._write_stub(".spec/scripts/validate_protocol_drift.py", 1)

        result = self._run("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("BLOCK", result.stderr)

    def test_cambios_menores_touched_runs_the_checks(self) -> None:
        (self.repo / ".spec" / "cambios-menores").mkdir(parents=True, exist_ok=True)
        (self.repo / ".spec" / "cambios-menores" / "2026-09-24-x.md").write_text(
            "## 2026-09-24 — x\n- qué: x\n- por qué: x\n- evidencia: x\n- gate: x\n"
        )
        _git(self.repo, "add", ".spec/cambios-menores/2026-09-24-x.md")
        self._write_stub(".spec/scripts/generate_minor_changes_index.py", 1)

        result = self._run("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("BLOCK", result.stderr)

    def test_missing_check_script_is_skipped_not_blocked(self) -> None:
        (self.repo / ".claude" / "agents").mkdir(parents=True)
        (self.repo / ".claude" / "agents" / "sdd-x.md").write_text("---\nmodel: sonnet\n---\n")
        _git(self.repo, "add", ".claude/agents/sdd-x.md")
        (self.repo / "scripts" / "materialize_claude_agents.py").unlink()

        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS", result.stdout)

    def _hooks_dir(self, cwd: Path) -> Path:
        """`git rev-parse --git-path hooks` is relative to `cwd` for a plain
        repo and absolute for a linked worktree (its `GIT_DIR` is always
        absolute) -- resolve it against `cwd` either way."""
        raw = _git(cwd, "rev-parse", "--git-path", "hooks").stdout.strip()
        path = Path(raw)
        return path if path.is_absolute() else (cwd / path)

    def test_install_creates_a_symlink_at_the_resolved_hooks_dir(self) -> None:
        result = self._run("--install")
        self.assertEqual(result.returncode, 0, result.stderr)

        hook_path = self._hooks_dir(self.repo) / "pre-commit"
        self.assertTrue(hook_path.is_symlink(), hook_path)
        self.assertEqual(hook_path.resolve(), self.script.resolve())

    def test_install_from_a_linked_worktree_resolves_the_shared_hooks_dir(self) -> None:
        worktree_dir = Path(self._tmp.name) / "wt"
        _git(self.repo, "worktree", "add", "-q", str(worktree_dir))

        result = self._run("--install", cwd=worktree_dir)
        self.assertEqual(result.returncode, 0, result.stderr)

        hooks_dir = self._hooks_dir(worktree_dir)
        hook_path = hooks_dir / "pre-commit"
        self.assertTrue(hook_path.is_symlink(), hook_path)
        # Hooks are shared, not duplicated per worktree: the resolved dir
        # lives under the main repo's `.git`, not under the worktree.
        self.assertTrue(str(hooks_dir.resolve()).startswith(str((self.repo / ".git").resolve())))


if __name__ == "__main__":
    unittest.main()
