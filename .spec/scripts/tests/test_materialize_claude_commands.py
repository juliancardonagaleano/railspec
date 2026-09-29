"""Tests for ``installer.materializers.commands`` (formerly
``scripts/materialize_claude_commands.py``).

Migrated to import the canonical module from
``installer/materializers/commands.py`` (unit 0006); the shim at
``scripts/materialize_claude_commands.py`` is verified separately by
``test_skills_reference_scripts.py`` and the manual smoke test in
``paquete-aprobacion.md``.

Spins up a temporary repo-like tree with ``.agents/commands/sample.md`` and a
stale ``.claude/commands/`` mirror, then drives the CLI through subprocess to
verify:
  1. byte-for-byte regeneration onto fixtures with the generated header
     line prepended (no drift after the run),
  2. SHA pinning updates when the canonical source changes,
  3. ``--check`` exit codes (0 = no drift, 1 = drift, 2 = error),
  4. idempotency — a second consecutive run does not mutate ``materialized_at``
     while the manifest sits within the idempotency window,
  5. orphan pruning — a mirror file/manifest entry whose source disappeared
     is removed,
  6. the mirror file set matches the source file set (minus the manifest).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "installer") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "installer"))

from materializers import commands as mcc  # noqa: E402

SCRIPT = SCRIPTS_ROOT / "materialize_claude_commands.py"

SAMPLE_COMMAND = """---
description: "Comando de prueba para materialize_claude_commands.py"
---

# /sample

Comando de prueba para materialize_claude_commands.py.
"""

SAMPLE_COMMAND_V2 = """---
description: "Comando de prueba para materialize_claude_commands.py"
---

# /sample

Comando de prueba para materialize_claude_commands.py (v2).

## Cambios

- bumped body en v2.
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
    """Populate a tmp repo with one canonical command and an empty mirror dir.

    Returns the manifest path.
    """
    agents = root / ".agents" / "commands"
    mirror = root / ".claude" / "commands"
    agents.mkdir(parents=True)
    mirror.mkdir(parents=True)
    (agents / "sample.md").write_text(SAMPLE_COMMAND, encoding="utf-8")
    return root / ".claude" / "commands" / ".claude-commands-manifest.yaml"


class MaterializeClaudeCommandsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="materialize-claude-commands-")
        self.root = Path(self._tmp)
        self.manifest = _seed_fixture(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _cli_args(self) -> list[str]:
        return [
            "--repo-root", str(self.root),
            "--source-dir", str(self.root / ".agents" / "commands"),
            "--mirror-dir", str(self.root / ".claude" / "commands"),
            "--manifest", str(self.manifest),
        ]

    def _stale_mirror(self) -> Path:
        """Plant a mirror with stale content to force a regen on the next run."""
        mirror_cmd = self.root / ".claude" / "commands" / "sample.md"
        mirror_cmd.parent.mkdir(parents=True, exist_ok=True)
        mirror_cmd.write_bytes(b"# stale\n")
        return mirror_cmd

    def _read_manifest(self) -> dict:
        return mcc.load_manifest(self.manifest)

    def test_1_regenerates_byte_for_byte_with_header(self) -> None:
        # Arrange: stale mirror so the script must overwrite it.
        mirror = self._stale_mirror()

        # Act: run without flags — should write the mirror and manifest.
        result = _run(self._cli_args())
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        expected = mcc.rendered_mirror_bytes(SAMPLE_COMMAND.encode(), "sample.md")
        self.assertEqual(mirror.read_bytes(), expected)

        # The frontmatter delimiter opens the file (line 0) — the generated
        # header must never precede it, or command-loading frontmatter
        # parsers that expect '---' on line 1 silently fail.
        mirror_lines = mirror.read_text(encoding="utf-8").splitlines()
        self.assertEqual(mirror_lines[0], "---")
        header_line = next(line for line in mirror_lines if "generado por" in line)
        self.assertIn(
            "generado por installer/materializers/commands.py desde "
            ".agents/commands/sample.md",
            header_line,
        )
        self.assertIn("no editar a mano", header_line)
        # And it comes right after the frontmatter's closing '---'.
        header_index = mirror_lines.index(header_line)
        self.assertEqual(mirror_lines[header_index - 1], "---")
        source = self.root / ".agents" / "commands" / "sample.md"
        self.assertNotIn("generado por", source.read_text(encoding="utf-8"))

        # Hash consistency between file and manifest entry.
        mirror_hash = hashlib.sha256(mirror.read_bytes()).hexdigest()
        manifest = self._read_manifest()
        entry = next(e for e in manifest["files"] if e["path"] == "sample.md")
        self.assertEqual(entry["sha256"], mirror_hash)
        self.assertEqual(manifest["source"], ".agents/commands")
        self.assertIsNotNone(manifest["materialized_at"])

    def test_2_sha_updates_when_canonical_changes(self) -> None:
        first = _run(self._cli_args())
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        manifest_v1 = self._read_manifest()
        sha_v1 = next(
            e["sha256"] for e in manifest_v1["files"] if e["path"] == "sample.md"
        )

        cmd_src = self.root / ".agents" / "commands" / "sample.md"
        cmd_src.write_text(SAMPLE_COMMAND_V2, encoding="utf-8")
        manifest_v1["materialized_at"] = "2000-01-01T00:00:00Z"
        mcc.write_manifest(self.manifest, manifest_v1)

        second = _run(self._cli_args())
        self.assertEqual(second.returncode, 0, msg=second.stderr)

        manifest_v2 = self._read_manifest()
        sha_v2 = next(
            e["sha256"] for e in manifest_v2["files"] if e["path"] == "sample.md"
        )
        expected_v2 = hashlib.sha256(
            mcc.rendered_mirror_bytes(SAMPLE_COMMAND_V2.encode(), "sample.md")
        ).hexdigest()
        self.assertEqual(sha_v2, expected_v2)
        self.assertNotEqual(sha_v2, sha_v1, "SHA must change when canonical source changes")

    def test_3_check_exit_codes(self) -> None:
        # 3a — no drift → exit 0.
        _run(self._cli_args())
        clean = _run([*self._cli_args(), "--check"])
        self.assertEqual(clean.returncode, 0, msg=clean.stderr)

        # 3b — drift → exit 1.
        self._stale_mirror()
        dirty = _run([*self._cli_args(), "--check"])
        self.assertEqual(dirty.returncode, 1, msg=dirty.stderr)

        # 3c — operational error (no source commands) → exit 2.
        shutil.rmtree(self.root / ".agents" / "commands")
        err = _run([*self._cli_args(), "--check"])
        self.assertEqual(err.returncode, 2, msg=err.stderr)

    def test_4_idempotency_does_not_bump_within_window(self) -> None:
        first = _run(self._cli_args())
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        ts_before = self._read_manifest()["materialized_at"]
        manifest_md5_before = hashlib.md5(self.manifest.read_bytes()).hexdigest()

        second = _run(self._cli_args())
        self.assertEqual(second.returncode, 0, msg=second.stderr)
        ts_after = self._read_manifest()["materialized_at"]
        manifest_md5_after = hashlib.md5(self.manifest.read_bytes()).hexdigest()

        self.assertEqual(ts_before, ts_after)
        self.assertEqual(manifest_md5_before, manifest_md5_after)
        self.assertIn("idempotency window", second.stdout)

    def test_5_orphan_pruning(self) -> None:
        first = _run(self._cli_args())
        self.assertEqual(first.returncode, 0, msg=first.stderr)

        # Rename source, forcing the old mirror/manifest entry to be an
        # orphan on the next run.
        (self.root / ".agents" / "commands" / "sample.md").rename(
            self.root / ".agents" / "commands" / "renamed.md"
        )
        manifest_v1 = self._read_manifest()
        manifest_v1["materialized_at"] = "2000-01-01T00:00:00Z"
        mcc.write_manifest(self.manifest, manifest_v1)

        # --check must see the orphan as drift.
        dirty = _run([*self._cli_args(), "--check"])
        self.assertEqual(dirty.returncode, 1, msg=dirty.stderr)

        second = _run(self._cli_args())
        self.assertEqual(second.returncode, 0, msg=second.stderr)

        self.assertFalse((self.root / ".claude" / "commands" / "sample.md").is_file())
        self.assertTrue((self.root / ".claude" / "commands" / "renamed.md").is_file())
        manifest_v2 = self._read_manifest()
        paths = {e["path"] for e in manifest_v2["files"]}
        self.assertNotIn("sample.md", paths)
        self.assertIn("renamed.md", paths)

        clean = _run([*self._cli_args(), "--check"])
        self.assertEqual(clean.returncode, 0, msg=clean.stderr)

    def test_6_mirror_name_set_matches_source_minus_manifest(self) -> None:
        (self.root / ".agents" / "commands" / "second.md").write_text(
            "---\ndescription: \"segundo comando de prueba\"\n---\n\n# /second\n",
            encoding="utf-8",
        )
        result = _run(self._cli_args())
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        source_names = {
            p.name for p in (self.root / ".agents" / "commands").iterdir() if p.is_file()
        }
        mirror_names = {
            p.name
            for p in (self.root / ".claude" / "commands").iterdir()
            if p.is_file() and p.name != ".claude-commands-manifest.yaml"
        }
        self.assertEqual(source_names, mirror_names)


if __name__ == "__main__":
    unittest.main()
