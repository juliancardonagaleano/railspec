"""CLI entry point: the help option exits 0 and prints the names of the
operations it supports, and that set of names is a stable, parseable pair.
The full comparison against `.spec/README.md`'s own operations table is a
separate test, added once that table exists."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLI_PATH = REPO_ROOT / "installer" / "cli.py"

_OPERATIONS_LINE = re.compile(r"Operaciones:\s*(.+)")


def _run_help() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI_PATH), "--help"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def operation_names(stdout: str) -> set[str]:
    for line in stdout.splitlines():
        match = _OPERATIONS_LINE.search(line)
        if match:
            return {name.strip() for name in match.group(1).split(",") if name.strip()}
    return set()


def test_ca01_help_exits_zero_and_prints_operation_names() -> None:
    result = _run_help()
    assert result.returncode == 0
    names = operation_names(result.stdout)
    assert names, f"no se encontró una línea 'Operaciones: ...' en:\n{result.stdout}"


def test_ca22_operation_name_set_is_stable() -> None:
    result = _run_help()
    names = operation_names(result.stdout)
    assert names == {"verificar", "instalar"}
