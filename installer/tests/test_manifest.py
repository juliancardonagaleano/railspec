"""Direct assertions over the loaded kit manifest: parses with non-empty
entries, declares the pre-push hook surface, and excludes the git dir,
agent-contract files, and the state/mirror surfaces from its payload."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from installer.manifest import DEFAULT_MANIFEST_PATH, ManifestEntry, load_manifest  # noqa: E402

_SPEC_DIR = ".spec/"
EXCLUDED_DEST_PREFIXES = (
    _SPEC_DIR + "units/",
    _SPEC_DIR + "planes/",
    _SPEC_DIR + "cambios-menores/",
    ".claude/skills/",
    ".claude/agents/",
    ".claude/commands/",
)
EXCLUDED_DEST_PATTERN = re.compile(r"^\.spec/perfiles\.[^/]*\.yaml$")


def _entries() -> list[ManifestEntry]:
    """Only parses and checks shape — no existence check on `source` here.
    Source existence is exercised elsewhere against the full install
    operation, not as a bare assertion over the manifest."""
    return load_manifest(DEFAULT_MANIFEST_PATH)


def test_ca04_manifest_parses_with_nonempty_entries() -> None:
    entries = _entries()
    assert len(entries) >= 1
    for entry in entries:
        assert entry.source.strip() != ""
        assert entry.dest.strip() != ""


def test_ca05_manifest_declares_pre_push_hook_surface() -> None:
    entries = _entries()
    dests = {entry.dest for entry in entries}
    assert "scripts/install_pre_push_hook.sh" in dests
    assert ".spec/scripts/pre-push-gate.sh" in dests


def test_ca06_no_dest_under_dot_git() -> None:
    entries = _entries()
    for entry in entries:
        assert not entry.dest.startswith(".git/"), entry.dest


def test_ca07_no_dest_is_instructions_md() -> None:
    """AGENTS.md sí puede figurar (carga de gobernanza CA-53, scope delta
    2026-09-29): es prosa que el kit produce y entrega al destino. Solo
    ``INSTRUCTIONS.md`` queda prohibido (no se usa en este kit).
    """
    entries = _entries()
    for entry in entries:
        name = entry.dest.rsplit("/", 1)[-1]
        assert name != "INSTRUCTIONS.md", entry.dest


def test_ca08_no_dest_falls_in_excluded_surfaces() -> None:
    entries = _entries()
    for entry in entries:
        assert not entry.dest.startswith(EXCLUDED_DEST_PREFIXES), entry.dest
        assert not EXCLUDED_DEST_PATTERN.match(entry.dest), entry.dest
