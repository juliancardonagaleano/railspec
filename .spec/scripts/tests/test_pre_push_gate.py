"""Tests for `pre-push-gate.sh` (unit 0114, scope addition 2026-09-20): a
push touching a protocol path is blocked unless `.spec/.pilot-verde` names
an ancestor commit; a push that doesn't touch protocol paths always passes;
the script never runs the protocol's regression suite itself.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / ".spec" / "scripts" / "pre-push-gate.sh"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


def _run_gate(cwd: Path, since: str, until: str = "HEAD") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), "--check", since, until],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


class PrePushGateTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory(prefix="pre-push-gate-")
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Test")
        (self.repo / "README.md").write_text("x\n")
        _git(self.repo, "add", "README.md")
        _git(self.repo, "commit", "-q", "-m", "base")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _commit_file(self, rel_path: str, content: str, message: str) -> str:
        path = self.repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        _git(self.repo, "add", rel_path)
        _git(self.repo, "commit", "-q", "-m", message)
        return _git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def test_no_protocol_path_touched_passes_without_marker(self) -> None:
        base = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._commit_file("docs/notes.md", "hola\n", "non-protocol change")
        result = _run_gate(self.repo, base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_protocol_path_touched_without_marker_blocks(self) -> None:
        base = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._commit_file(
            ".agents/skills/sdd-gate/SKILL.md", "# gate\n", "edit protocol skill"
        )
        result = _run_gate(self.repo, base)
        self.assertEqual(result.returncode, 1)
        self.assertIn("BLOCK", result.stderr)
        self.assertIn(".spec/.pilot-verde", result.stderr)

    def test_marker_naming_a_non_ancestor_still_blocks(self) -> None:
        base = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        starting_branch = _git(self.repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
        # A commit on a sibling branch, never merged into the pushed history —
        # genuinely not an ancestor of what is being pushed.
        _git(self.repo, "checkout", "-q", "-b", "sibling")
        (self.repo / "sibling.md").write_text("s\n")
        _git(self.repo, "add", "sibling.md")
        _git(self.repo, "commit", "-q", "-m", "sibling commit")
        non_ancestor = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "checkout", "-q", starting_branch)

        (self.repo / ".spec").mkdir(exist_ok=True)
        (self.repo / ".spec" / ".pilot-verde").write_text(non_ancestor + "\n")
        self._commit_file(
            ".agents/skills/sdd-gate/SKILL.md", "# gate\n", "edit protocol skill"
        )
        result = _run_gate(self.repo, base)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no es ancestro", result.stderr)

    def test_marker_naming_an_ancestor_passes(self) -> None:
        base = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        (self.repo / ".spec").mkdir(exist_ok=True)
        (self.repo / ".spec" / ".pilot-verde").write_text(base + "\n")
        self._commit_file(
            ".agents/skills/sdd-gate/SKILL.md", "# gate\n", "edit protocol skill"
        )
        result = _run_gate(self.repo, base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_never_invokes_the_regression_suite_itself(self) -> None:
        # The only mentions of running the suite are inside comments/echo
        # strings telling the human what to run, never a bare invocation
        # the script executes on its own.
        script_text = SCRIPT.read_text(encoding="utf-8")
        # The literal is the *current* remediation command on purpose (CA-13):
        # if a future change to what the human must run forgot to update this
        # assertion, it would keep searching for a string the script no
        # longer prints anywhere, passing trivially without covering
        # anything. (Would have failed against the retired literal
        # `supervised-test.sh correr --todos`, which this script no longer
        # cites at all.)
        self.assertIn("pytest .spec/scripts/tests", script_text,
                      "pre-push-gate.sh ya no cita la suite de regresión en absoluto")
        exec_lines = [
            ln for ln in script_text.splitlines()
            if "pytest .spec/scripts/tests" in ln and not ln.strip().startswith("#")
            and "echo" not in ln
        ]
        self.assertEqual(exec_lines, [])


if __name__ == "__main__":
    unittest.main()
