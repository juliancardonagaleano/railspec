"""Resolve the git worktree root and git directory of an install target."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitTargetError(Exception):
    """The target is not the root of a git worktree, or git could not be
    invoked against it."""


def _run_git(target: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(target), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitTargetError(
            f"git {' '.join(args)} falló para {target}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def resolve_git_root(target: Path) -> Path:
    """Return the absolute root of the git worktree at ``target``.
    Raises ``GitTargetError`` when ``target`` is not itself the root of a
    git worktree — a subdirectory of a valid worktree must be rejected too,
    not silently redirected to that worktree's real root."""
    if not target.is_dir():
        raise GitTargetError(f"el destino no es un directorio: {target}")
    try:
        toplevel = _run_git(target, "rev-parse", "--show-toplevel")
    except GitTargetError as exc:
        raise GitTargetError(f"el destino no es raíz de un árbol de trabajo git: {target}") from exc
    resolved_toplevel = Path(toplevel).resolve()
    # os.path.samefile compares by inode/device, not by string equality of
    # the resolved path — a target passed with a different case than the
    # one on disk (case-insensitive filesystems, the default on macOS and
    # Windows) is still the same directory and must not be rejected as a
    # subfolder of itself.
    if not os.path.samefile(resolved_toplevel, target):
        raise GitTargetError(
            f"el destino no es raíz de un árbol de trabajo git: {target} "
            f"(es una subcarpeta de {resolved_toplevel})"
        )
    return resolved_toplevel


def resolve_git_dir(git_root: Path) -> Path:
    """Return the absolute git directory of ``git_root`` (what ``git
    rev-parse --absolute-git-dir`` reports). Anything sdd-kit needs to keep
    local to a worktree without it counting as part of the installed
    payload belongs here — e.g. the install record."""
    return Path(_run_git(git_root, "rev-parse", "--absolute-git-dir"))
