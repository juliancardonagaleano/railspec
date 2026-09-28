"""Mechanical grep checks over the implementation of sdd-kit: the entry
point, its modules, and the kit manifest — the set the glossary of this
tool's own spec calls "the implementation" (point of entry, modules, and
the kit manifest; test files are not part of that set, which is what lets
this very file declare the literals it looks for without matching itself).

Two closed lists, each checked with `grep -rIF`, one execution per literal:
four sibling-monorepo literals that must never appear (no dependency on any
other checkout), and three third-party-tool literals that must never
appear either (sdd-kit inspects nothing but its own manifest and the
target's git worktree — the sole declared exception is invoking the `git`
executable itself, which none of these seven literals name)."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SDD_KIT_DIR = REPO_ROOT / "installer"

# "La implementación de sdd-kit": punto de entrada, módulos, manifiesto del
# kit. No incluye sdd-kit/tests/ — ver docstring del módulo.
IMPLEMENTATION_PATHS = (
    SDD_KIT_DIR / "cli.py",
    SDD_KIT_DIR / "manifest.py",
    SDD_KIT_DIR / "git_target.py",
    SDD_KIT_DIR / "verifier.py",
    SDD_KIT_DIR / "installer.py",
    SDD_KIT_DIR / "kit_manifest.yaml",
)

SIBLING_MONOREPO_LITERALS = (
    "framework-c3",
    "monorepo hermano",
    "materialize_sdd_kit",
    "knowledge/.sdd-kit",
)

THIRD_PARTY_TOOL_LITERALS = (
    "gitnexus",
    "codebase-memory-mcp",
    "claude mcp",
)


def _grep_exit_code(literal: str) -> int:
    result = subprocess.run(
        ["grep", "-rIF", "-e", literal, *[str(p) for p in IMPLEMENTATION_PATHS]],
        capture_output=True,
        text=True,
    )
    return result.returncode


def test_ca02_no_sibling_monorepo_literals() -> None:
    for literal in SIBLING_MONOREPO_LITERALS:
        assert _grep_exit_code(literal) == 1, f"literal encontrado en la implementación: {literal!r}"


def test_ca23_no_third_party_tool_literals() -> None:
    for literal in THIRD_PARTY_TOOL_LITERALS:
        assert _grep_exit_code(literal) == 1, f"literal encontrado en la implementación: {literal!r}"
