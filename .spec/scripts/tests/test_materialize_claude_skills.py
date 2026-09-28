"""Tests for ``scripts/materialize_claude_skills.py`` (unit 0131, G5 — T15/T16).

Spins up a temporary repo-like tree with ``.agents/skills/sdd-test/SKILL.md``
and a stale ``.claude/skills/`` mirror, then drives the CLI through subprocess
to verify:
  1. byte-for-byte regeneration onto fixtures (no drift after the run),
  2. SHA pinning updates when the canonical source changes,
  3. ``--check`` exit codes (0 = no drift, 1 = drift, 2 = error),
  4. idempotency — a second consecutive run does not mutate ``materialized_at``
     while the manifest sits within the idempotency window.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import materialize_claude_skills as mcs  # noqa: E402

SCRIPT = SCRIPTS_DIR / "materialize_claude_skills.py"

SAMPLE_SKILL = """---
description: Sample skill for testing materialize_claude_skills.py
---

# Sample skill

This file exists only inside test fixtures; nothing here is real guidance.
"""

SAMPLE_SKILL_V2 = """---
description: Sample skill for testing materialize_claude_skills.py (v2)
---

# Sample skill

This file exists only inside test fixtures; nothing here is real guidance.

## Changelog

- bumped description in v2.
"""


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd) if cwd else None,
    )


def _seed_fixture(root: Path) -> Path:
    """Populate a tmp repo with one sdd-* skill and a stale mirror.

    Returns the manifest path.
    """
    agents = root / ".agents" / "skills"
    mirror = root / ".claude" / "skills"
    agents.mkdir(parents=True)
    mirror.mkdir(parents=True)
    skill_dir = agents / "sdd-test"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")
    return root / ".claude" / "skills" / ".claude-skills-manifest.yaml"


class MaterializeClaudeSkillsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="materialize-claude-skills-")
        self.root = Path(self._tmp)
        self.manifest = _seed_fixture(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _cli_args(self) -> list[str]:
        return [
            "--repo-root", str(self.root),
            "--source-dir", str(self.root / ".agents" / "skills"),
            "--mirror-dir", str(self.root / ".claude" / "skills"),
            "--manifest", str(self.manifest),
        ]

    def _add_reference_file(self) -> Path:
        """Add a second file alongside ``SKILL.md`` inside the canonical skill."""
        ref = self.root / ".agents" / "skills" / "sdd-test" / "references" / "rubrica.md"
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text("# rubrica de prueba\n", encoding="utf-8")
        return ref

    def _stale_mirror(self) -> Path:
        """Plant a mirror with stale content to force a regen on the next run."""
        mirror_skill = self.root / ".claude" / "skills" / "sdd-test" / "SKILL.md"
        mirror_skill.parent.mkdir(parents=True, exist_ok=True)
        mirror_skill.write_bytes(b"# stale\n")
        return mirror_skill

    def _read_manifest(self) -> dict:
        return mcs.load_manifest(self.manifest)

    def test_1_regenerates_byte_for_byte(self) -> None:
        # Arrange: stale mirror so the script must overwrite it.
        mirror = self._stale_mirror()
        self.assertNotEqual(mirror.read_bytes(), SAMPLE_SKILL.encode())

        # Act: run without flags — should write the mirror and manifest.
        result = _run(self._cli_args())

        # Assert CLI succeeded.
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        # Mirror now byte-for-byte equal to source.
        self.assertEqual(mirror.read_bytes(), SAMPLE_SKILL.encode())

        # Hash consistency between file and manifest entry.
        mirror_hash = hashlib.sha256(mirror.read_bytes()).hexdigest()
        manifest = self._read_manifest()
        entry = next(e for e in manifest["files"] if e["path"] == "sdd-test/SKILL.md")
        self.assertEqual(entry["sha256"], mirror_hash)
        self.assertEqual(manifest["source"], ".agents/skills")
        self.assertIsNotNone(manifest["materialized_at"])

    def test_2_sha_updates_when_canonical_changes(self) -> None:
        # First run with the v1 source.
        first = _run(self._cli_args())
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        manifest_v1 = self._read_manifest()
        sha_v1 = next(
            e["sha256"] for e in manifest_v1["files"]
            if e["path"] == "sdd-test/SKILL.md"
        )

        # Mutate canonical source; bump clock past the idempotency window by
        # rewriting the manifest's `materialized_at` to a stale value so the
        # script does not short-circuit.
        skill_src = self.root / ".agents" / "skills" / "sdd-test" / "SKILL.md"
        skill_src.write_text(SAMPLE_SKILL_V2, encoding="utf-8")
        manifest_v1["materialized_at"] = "2000-01-01T00:00:00Z"
        mcs.write_manifest(self.manifest, manifest_v1)

        # Second run — must observe v2 SHA.
        second = _run(self._cli_args())
        self.assertEqual(second.returncode, 0, msg=second.stderr)

        manifest_v2 = self._read_manifest()
        sha_v2 = next(
            e["sha256"] for e in manifest_v2["files"]
            if e["path"] == "sdd-test/SKILL.md"
        )
        expected_v2 = hashlib.sha256(SAMPLE_SKILL_V2.encode()).hexdigest()
        self.assertEqual(sha_v2, expected_v2)
        self.assertNotEqual(sha_v2, sha_v1, "SHA must change when canonical source changes")

    def test_2b_mirrors_files_beyond_skill_md(self) -> None:
        # A skill's references/ (or any other file alongside SKILL.md) must
        # mirror too — not only SKILL.md itself.
        ref_src = self._add_reference_file()

        result = _run(self._cli_args())
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        ref_mirror = self.root / ".claude" / "skills" / "sdd-test" / "references" / "rubrica.md"
        self.assertTrue(ref_mirror.is_file())
        self.assertEqual(ref_mirror.read_bytes(), ref_src.read_bytes())

        manifest = self._read_manifest()
        entry = next(
            e for e in manifest["files"] if e["path"] == "sdd-test/references/rubrica.md"
        )
        self.assertEqual(entry["sha256"], hashlib.sha256(ref_src.read_bytes()).hexdigest())

        # --check must now see it as in sync, not as drift.
        clean = _run([*self._cli_args(), "--check"])
        self.assertEqual(clean.returncode, 0, msg=clean.stderr)

    def test_3_check_exit_codes(self) -> None:
        # 3a — no drift → exit 0.
        _run(self._cli_args())  # materialize once so mirror is in sync
        clean = _run([*self._cli_args(), "--check"])
        self.assertEqual(clean.returncode, 0, msg=clean.stderr)

        # 3b — drift → exit 1.
        self._stale_mirror()
        dirty = _run([*self._cli_args(), "--check"])
        self.assertEqual(dirty.returncode, 1, msg=dirty.stderr)

        # 3c — operational error (no source skills) → exit 2.
        shutil.rmtree(self.root / ".agents" / "skills")
        err = _run([*self._cli_args(), "--check"])
        self.assertEqual(err.returncode, 2, msg=err.stderr)

    def test_4_idempotency_does_not_bump_within_window(self) -> None:
        # First run: primes the manifest with a current timestamp.
        first = _run(self._cli_args())
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        ts_before = self._read_manifest()["materialized_at"]
        manifest_md5_before = hashlib.md5(self.manifest.read_bytes()).hexdigest()

        # Second run, immediately after, with no source changes: must NOT
        # rewrite the manifest.
        second = _run(self._cli_args())
        self.assertEqual(second.returncode, 0, msg=second.stderr)
        ts_after = self._read_manifest()["materialized_at"]
        manifest_md5_after = hashlib.md5(self.manifest.read_bytes()).hexdigest()

        self.assertEqual(ts_before, ts_after)
        self.assertEqual(manifest_md5_before, manifest_md5_after)
        self.assertIn("idempotency window", second.stdout)


if __name__ == "__main__":
    unittest.main()
