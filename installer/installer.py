"""sdd-kit's install operation: writes the kit payload to a target, then
regenerates its mirrors and installs the pre-push hook there.

Conflict semantics: a path is classified as a conflict if it exists in the
target and its content differs from its source — regardless of whether a
previous-install record is present there. That record is bookkeeping only
and never read for this classification. Any conflict aborts the whole run
(exit 3) unless ``--force`` authorizes overwriting it; either way, the
manifest, every source and the target's git-worktree-root status are
validated before a single byte is written (exit 2 on any of those, with no
effect on the target).

Orchestrates the three existing mirror materializers as subprocesses against
the target (``--repo-root <target>``) instead of re-implementing their
copy/manifest/drift logic, and reuses ``scripts/install_pre_push_hook.sh``
for the pre-push hook instead of reimplementing its idempotency or its
foreign-hook detection.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from git_target import GitTargetError, resolve_git_dir, resolve_git_root
from manifest import DEFAULT_MANIFEST_PATH, ManifestError, load_and_validate
from verifier import iter_payload_files

EXIT_OK = 0
EXIT_OPERATIONAL_ERROR = 2
EXIT_CONFLICT = 3

# Lives under the git directory, never under the worktree proper: it is not
# part of the carga (no manifest entry names it) and, by living under
# `.git/`, it never shows up as an unaccounted-for path in a plain listing of
# the worktree's own files (such a listing conventionally excludes
# everything under `.git/`).
INSTALL_RECORD_NAME = "sdd-kit-install-record.yaml"

# Regenerated in the target as subprocesses, in this order, once the payload
# is written. Each script already supports ``--repo-root`` (it derives its
# own source/mirror/manifest paths from it).
MIRROR_MATERIALIZERS = (
    "scripts/materialize_claude_skills.py",
    "scripts/materialize_claude_agents.py",
    "scripts/materialize_claude_commands.py",
)

PRE_PUSH_HOOK_INSTALLER = "scripts/install_pre_push_hook.sh"


def _classify_conflicts(pairs: list[tuple[Path, str]], target: Path) -> list[str]:
    """Return the dest-relative paths, in payload order, that are present in
    the target with content that differs from their source. A path absent
    from the target is not a conflict — it is simply written below. Presence
    or absence of the previous-install record never enters this
    comparison."""
    conflicts: list[str] = []
    for source_file, dest_rel in pairs:
        dest_path = target / dest_rel
        if dest_path.is_file() and dest_path.read_bytes() != source_file.read_bytes():
            conflicts.append(dest_rel)
    return conflicts


def _write_payload(pairs: list[tuple[Path, str]], target: Path) -> None:
    for source_file, dest_rel in pairs:
        dest_path = target / dest_rel
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(source_file.read_bytes())


def _run_materializer(script_rel: str, target: Path) -> int:
    script_path = target / script_rel
    result = subprocess.run(
        # -B: never write __pycache__/*.pyc into the target as a side
        # effect of importing its own modules (e.g. effort_profile.py) —
        # the target ends up with exactly the payload plus the three
        # mirrors, nothing else.
        [sys.executable, "-B", str(script_path), "--repo-root", str(target)],
        cwd=target,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def _install_pre_push_hook(target: Path) -> int:
    # No ``--install`` flag exists on this script: its default action (no
    # args) already installs, and re-running it is a no-op when the hook it
    # finds already carries its own marker — the idempotency this operation
    # relies on for repeated installs against the same target.
    hook_installer = target / PRE_PUSH_HOOK_INSTALLER
    result = subprocess.run(
        ["bash", str(hook_installer)],
        cwd=target,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def _write_install_record(git_root: Path) -> None:
    record_path = resolve_git_dir(git_root) / INSTALL_RECORD_NAME
    record_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record_path.write_text(
        "# GENERADO por sdd-kit — bookkeeping de diagnóstico, nunca leído para\n"
        "# clasificar conflictos de una corrida futura.\n"
        f'installed_at: "{timestamp}"\n',
        encoding="utf-8",
    )


def run_install(
    target: Path, *, force: bool = False, manifest_path: Path = DEFAULT_MANIFEST_PATH
) -> int:
    try:
        entries = load_and_validate(manifest_path)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL_ERROR

    try:
        git_root = resolve_git_root(target)
    except GitTargetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL_ERROR

    pairs = iter_payload_files(entries)
    conflicts = _classify_conflicts(pairs, git_root)

    if conflicts and not force:
        for rel in conflicts:
            print(rel)
        print(
            f"ERROR: {len(conflicts)} ruta(s) en conflicto con ediciones "
            "manuales del destino; usar --force para sobrescribirlas.",
            file=sys.stderr,
        )
        return EXIT_CONFLICT

    _write_payload(pairs, git_root)

    for script_rel in MIRROR_MATERIALIZERS:
        code = _run_materializer(script_rel, git_root)
        if code != 0:
            print(
                f"ERROR: {script_rel} falló con código {code} sobre {git_root}. "
                "La carga ya fue escrita en el destino antes de este fallo — a "
                "diferencia de los demás errores con este mismo código, el "
                "destino NO quedó intacto: la carga está, algunos espejos "
                "pueden estar sin regenerar, y falta el gancho de pre-push. "
                "Reintentar la instalación (o corregir la causa del fallo y "
                "volver a correrla) para completarla.",
                file=sys.stderr,
            )
            return EXIT_OPERATIONAL_ERROR

    hook_code = _install_pre_push_hook(git_root)
    if hook_code != 0:
        print(
            f"ERROR: instalación del gancho de pre-push falló con código {hook_code}. "
            "La carga y los espejos ya quedaron escritos en el destino antes "
            "de este fallo — el destino NO quedó intacto, solo falta el "
            "gancho. Reintentar la instalación para completarla.",
            file=sys.stderr,
        )
        return EXIT_OPERATIONAL_ERROR

    _write_install_record(git_root)

    print(f"OK: instalación completa en {git_root} ({len(pairs)} archivo(s))")
    return EXIT_OK
