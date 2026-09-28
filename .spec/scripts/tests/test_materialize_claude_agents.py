"""Tests for `scripts/materialize_claude_agents.py` (unit 0166, G1 — T5).

Spins up a temporary `.agents/agents/` with the 9 known roles' canonical
bodies and a small `perfiles.yaml`, then drives the CLI through subprocess
to verify: the (modelo, effort) pair `estandar` declares lands in the
generated frontmatter right after `description:`, the generated-file header
comment appears right after the frontmatter, exactly the variant set
`perfiles.yaml` requires is generated (no missing, no orphan — CA-26),
`--check` exit codes (0 no drift, 1 drift/orphan, 2 operational error), and
idempotency across consecutive runs.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "scripts"
SPEC_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_ROOT))
sys.path.insert(0, str(SPEC_SCRIPTS_DIR))

import materialize_claude_agents as mca  # noqa: E402
import effort_profile as ep  # noqa: E402

SCRIPT = SCRIPTS_ROOT / "materialize_claude_agents.py"

CANONICAL_BODY = """---
name: {role}
description: Cuerpo de prueba de {role} para materialize_claude_agents.py.
---

Cuerpo de prueba de {role}. Nada de esto es guía real.
"""

SAMPLE_PROFILES = """default: estandar

perfiles:
  estandar:
    roles:
      sdd-critico-cumplimiento: {modelo: sonnet, effort: high}
      sdd-critico-estructural: {modelo: haiku}
      sdd-critico-profundo: {modelo: sonnet, effort: high}
      sdd-especificar-redactor: {modelo: sonnet, effort: high}
      sdd-explorador: {modelo: sonnet, effort: medium}
      sdd-implementador: {modelo: sonnet, effort: high}
      sdd-planificar-redactor: {modelo: sonnet, effort: high}
      sdd-refutador: {modelo: sonnet, effort: high}
      sdd-tareas-redactor: {modelo: haiku}
    gate:
      bajo: {criticos: 1, iteraciones: 1, adversarial: false}
      medio: {criticos: 1, iteraciones: 1, adversarial: false}
      alto: {criticos: 2, iteraciones: 2, adversarial: true}
    exploradores:
      bajo: 1
      medio: 1
      alto: 3
    implementador_complejo: {modelo: sonnet, effort: xhigh}

  ligero:
    roles:
      sdd-critico-profundo: {modelo: haiku}
"""


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False,
    )


def _seed_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    """Returns (source_dir, mirror_dir, manifest_path, profiles_path)."""
    source_dir = root / ".agents" / "agents"
    mirror_dir = root / ".claude" / "agents"
    source_dir.mkdir(parents=True)
    mirror_dir.mkdir(parents=True)
    for role in ep.KNOWN_ROLES:
        (source_dir / f"{role}.md").write_text(CANONICAL_BODY.format(role=role), encoding="utf-8")
    profiles_path = root / "perfiles.yaml"
    profiles_path.write_text(SAMPLE_PROFILES, encoding="utf-8")
    manifest_path = mirror_dir / ".claude-agents-manifest.yaml"
    return source_dir, mirror_dir, manifest_path, profiles_path


class MaterializeClaudeAgentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="materialize-claude-agents-")
        self.root = Path(self._tmp)
        self.source_dir, self.mirror_dir, self.manifest, self.profiles_path = _seed_fixture(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _cli_args(self) -> list[str]:
        return [
            "--repo-root", str(self.root),
            "--source-dir", str(self.source_dir),
            "--mirror-dir", str(self.mirror_dir),
            "--manifest", str(self.manifest),
            "--profiles", str(self.profiles_path),
        ]

    def test_1_base_roles_get_model_effort_and_header(self) -> None:
        result = _run(self._cli_args())
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        generated = self.mirror_dir / "sdd-critico-profundo.md"
        self.assertTrue(generated.is_file())
        text = generated.read_text(encoding="utf-8")
        self.assertIn("model: sonnet", text)
        self.assertIn("effort: high", text)
        self.assertIn(
            "generado por scripts/materialize_claude_agents.py desde .spec/perfiles.yaml "
            "y .agents/agents/sdd-critico-profundo.md",
            text,
        )
        # header comment must sit right after the closing '---' of the frontmatter
        # (the delimiter pair: index 0 opens it, the second occurrence closes it).
        lines = text.splitlines()
        delimiter_indices = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
        self.assertEqual(len(delimiter_indices), 2)
        closing = delimiter_indices[1]
        self.assertTrue(lines[closing + 1].startswith("<!-- generado"))

    def test_2_haiku_role_has_no_effort_line(self) -> None:
        result = _run(self._cli_args())
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        text = (self.mirror_dir / "sdd-tareas-redactor.md").read_text(encoding="utf-8")
        self.assertIn("model: haiku", text)
        self.assertNotIn("effort:", text)

    def test_3_variant_has_one_line_description_and_same_body(self) -> None:
        result = _run(self._cli_args())
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        variant = self.mirror_dir / "sdd-implementador-xhigh.md"
        self.assertTrue(variant.is_file())
        text = variant.read_text(encoding="utf-8")
        self.assertIn("name: sdd-implementador-xhigh", text)
        self.assertIn("description: Variante xhigh de sdd-implementador.", text)
        self.assertIn("model: sonnet", text)
        self.assertIn("effort: xhigh", text)

        base = (self.mirror_dir / "sdd-implementador.md").read_text(encoding="utf-8")
        _, variant_body = mca.split_frontmatter(text)
        _, base_body = mca.split_frontmatter(base)
        self.assertEqual(variant_body, base_body)

    def test_4_variant_set_matches_required_variants_exactly(self) -> None:
        result = _run(self._cli_args())
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        profiles = ep.load_profiles(self.profiles_path)
        expected_variant_files = {
            f"{role}-{effort}.md" for role, effort, _modelo in ep.required_variants(profiles)
        }
        actual_files = {p.name for p in self.mirror_dir.glob("*.md")}
        base_files = {f"{role}.md" for role in ep.KNOWN_ROLES}
        self.assertEqual(actual_files - base_files, expected_variant_files)

        clean = _run([*self._cli_args(), "--check"])
        self.assertEqual(clean.returncode, 0, msg=clean.stderr)

    def test_5_check_exit_codes(self) -> None:
        # 5a — no drift after a real run.
        _run(self._cli_args())
        clean = _run([*self._cli_args(), "--check"])
        self.assertEqual(clean.returncode, 0, msg=clean.stderr)

        # 5b — drift (stale content) -> exit 1.
        stale = self.mirror_dir / "sdd-critico-profundo.md"
        stale.write_bytes(b"# stale\n")
        dirty = _run([*self._cli_args(), "--check"])
        self.assertEqual(dirty.returncode, 1, msg=dirty.stderr)

        # 5c — operational error: a role declared in perfiles.yaml has no
        # canonical body -> exit 2.
        (self.source_dir / "sdd-critico-profundo.md").unlink()
        err = _run([*self._cli_args(), "--check"])
        self.assertEqual(err.returncode, 2, msg=err.stderr)

    def test_6_missing_profiles_file_is_operational_error(self) -> None:
        (self.profiles_path).unlink()
        result = _run([*self._cli_args(), "--check"])
        self.assertEqual(result.returncode, 2, msg=result.stderr)
        self.assertIn(str(self.profiles_path), result.stderr)

    def test_7_orphan_variant_detected_and_pruned(self) -> None:
        _run(self._cli_args())
        orphan = self.mirror_dir / "sdd-critico-profundo-xhigh.md"
        orphan.write_text("---\nname: sdd-critico-profundo-xhigh\n---\nstale\n", encoding="utf-8")

        dirty = _run([*self._cli_args(), "--check"])
        self.assertEqual(dirty.returncode, 1, msg=dirty.stderr)

        prune = _run(self._cli_args())
        self.assertEqual(prune.returncode, 0, msg=prune.stderr)
        self.assertFalse(orphan.is_file())

        clean = _run([*self._cli_args(), "--check"])
        self.assertEqual(clean.returncode, 0, msg=clean.stderr)

    def test_8_idempotency_does_not_bump_within_window(self) -> None:
        first = _run(self._cli_args())
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        manifest_md5_before = hashlib.md5(self.manifest.read_bytes()).hexdigest()

        second = _run(self._cli_args())
        self.assertEqual(second.returncode, 0, msg=second.stderr)
        manifest_md5_after = hashlib.md5(self.manifest.read_bytes()).hexdigest()

        self.assertEqual(manifest_md5_before, manifest_md5_after)
        self.assertIn("idempotency window", second.stdout)


if __name__ == "__main__":
    unittest.main()
