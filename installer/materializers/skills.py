#!/usr/bin/env python3
"""Regenerate ``.claude/skills/`` from ``.agents/skills/``.

Originally ``scripts/materialize_claude_skills.py``; moved verbatim to
``installer/materializers/skills.py`` (unit 0006) so that
``installer/cli.py materialize skills`` and the legacy shim at
``scripts/materialize_claude_skills.py`` both reach the same ``main`` —
see CA-01, CA-03, CA-04.

Finds every ``<prefix>*/`` under ``.agents/skills/`` that contains a
``SKILL.md``, then copies ALL of that skill's files byte-for-byte to its
mirror under ``.claude/skills/<prefix>*/`` — ``SKILL.md`` and any other file
alongside it, such as a ``references/`` subdirectory — recomputes SHA-256 for
each and updates ``.claude/skills/.claude-skills-manifest.yaml`` so every
entry's SHA matches the regenerated mirror and ``materialized_at`` reflects
the most recent run.

Scope: directories that are not ``gitnexus-*`` AND that contain a ``SKILL.md``.
The gitnexus skill family is excluded because it has its own mirror
mechanism (served by the gitnexus runtime, not Claude Code/OpenCode
portable agents). All other portable skill families — including the new
``agent-tool-discipline`` from unit 0154 — are mirrored.

The manifest is additive with respect to scope: it only touches entries whose
path matches a materialized ``<prefix>*/SKILL.md``. Pre-existing entries from
other scopes (e.g. ``gitnexus-*``) are left untouched.

Pruning: within the same scope, any mirror file or manifest entry that no
longer has a counterpart under ``.agents/skills/`` (because the source file
was removed or renamed) is deleted from the mirror and dropped from the
manifest. This is symmetric with drift detection above — drift finds what the
source has and the mirror lacks or has stale; pruning finds what the mirror
has and the source no longer has. ``--check`` reports an orphan the same way
it reports drift: exit 1 until the mirror/manifest are pruned.

Idempotency window: when every freshly-computed SHA already matches the entry
in the manifest, there are no orphans to prune, *and* ``materialized_at`` is
younger than ``IDEMPOTENCY_WINDOW_SECONDS``, the script is a no-op and does
not rewrite files or bump the timestamp.

Exit codes: 0 OK (or no drift under ``--check``), 1 drift detected under
``--check``, 2 operational error (no source files, IO failure).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .ajenos import PREFIJO_RAILSPEC  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR = REPO_ROOT / ".agents" / "skills"
DEFAULT_MIRROR_DIR = REPO_ROOT / ".claude" / "skills"
DEFAULT_MANIFEST = DEFAULT_MIRROR_DIR / ".claude-skills-manifest.yaml"
MANIFEST_HEADER = "# GENERADO por installer/materializers/skills.py — no editar a mano.\n"
IDEMPOTENCY_WINDOW_SECONDS = 60
# Skill families whose mirror is owned by their own runtime (not Claude
# Code/OpenCode portable agents). Excluded from the portable mirror.
# ``railspec*`` are the skills Railspec's adapters write (``railspec`` and
# ``railspec-bucle``, also under ``.agents/skills/`` for Codex): they are not
# kit skills, so the mirror neither copies nor prunes them.
EXCLUDED_FAMILIES = ("gitnexus-", PREFIJO_RAILSPEC)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def discover_skills(source_dir: Path) -> list[Path]:
    """Return every file of every portable skill under ``source_dir``, sorted.

    A skill is portable if its directory name does NOT start with any prefix
    in ``EXCLUDED_FAMILIES`` and contains a ``SKILL.md`` file. Once a skill
    qualifies, ALL of its files mirror — not just ``SKILL.md`` — so a skill's
    ``references/`` subdirectory (extra rubrics, checklists) ships with it
    instead of silently drifting out of sync with the canonical source.
    """
    if not source_dir.is_dir():
        return []
    found: list[Path] = []
    for child in sorted(source_dir.iterdir()):
        if not child.is_dir():
            continue
        if any(child.name.startswith(prefix) for prefix in EXCLUDED_FAMILIES):
            continue
        if not (child / "SKILL.md").is_file():
            continue
        found.extend(sorted(p for p in child.rglob("*") if p.is_file()))
    return found


def detect_drift(source_dir: Path, mirror_dir: Path, skills: list[Path]) -> int:
    """Count missing or differing mirror files among ``skills``."""
    drift = 0
    for src in skills:
        rel = src.relative_to(source_dir)
        dst = mirror_dir / rel
        if not dst.is_file():
            drift += 1
            continue
        if src.read_bytes() != dst.read_bytes():
            drift += 1
    return drift


def find_orphans(
    source_dir: Path,
    mirror_dir: Path,
    manifest_path: Path,
    manifest: dict,
    skills: list[Path],
) -> tuple[list[Path], list[str]]:
    """Return mirror files and manifest entries with no counterpart in source.

    Scoped to portable families only (an in-scope top-level directory name
    does not start with any ``EXCLUDED_FAMILIES`` prefix) — a mirrored
    ``gitnexus-*`` family, or any other pre-existing manifest entry outside
    this script's scope, is never touched. A path counts as an orphan when
    (a) it is nested under a top-level directory (every legitimate skill file
    is, since ``discover_skills`` only walks ``<family>/**``), (b) that
    top-level directory is in scope, and (c) the exact relative path no
    longer exists under ``source_dir`` — the mirror or manifest side of
    ``detect_drift``'s complement. Requirement (a) is deliberate: a loose file
    directly at the mirror/manifest root (anything other than the manifest
    itself, which is excluded by identity) never belonged to any skill family
    and is therefore never eligible for pruning, even if scoped-in by name.
    """
    valid_rel = {src.relative_to(source_dir).as_posix() for src in skills}

    def is_prunable(rel: str) -> bool:
        if "/" not in rel:
            return False
        top = rel.split("/", 1)[0]
        in_scope = not any(top.startswith(prefix) for prefix in EXCLUDED_FAMILIES)
        return in_scope and rel not in valid_rel

    mirror_orphan_files: list[Path] = []
    if mirror_dir.is_dir():
        for path in sorted(mirror_dir.rglob("*")):
            if not path.is_file() or path == manifest_path:
                continue
            rel = path.relative_to(mirror_dir).as_posix()
            if is_prunable(rel):
                mirror_orphan_files.append(path)

    manifest_orphan_paths: list[str] = []
    for entry in manifest.get("files", []):
        rel = entry["path"]
        if is_prunable(rel):
            manifest_orphan_paths.append(rel)

    return mirror_orphan_files, manifest_orphan_paths


def prune_mirror_files(mirror_dir: Path, orphan_files: list[Path]) -> set[str]:
    """Delete orphan mirror files, then remove directories left empty.

    A per-file ``OSError`` (permission, read-only mount, …) is caught so one
    failure does not abort the batch mid-loop and leave the rest undeleted
    with no record of what happened; the caller only drops a manifest entry
    for a path that this function actually removed, so a failed delete stays
    represented in the manifest and gets retried — and re-reported — on the
    next run instead of silently going out of sync with the mirror on disk.
    Returns the set of relative (posix, to ``mirror_dir``) paths removed.
    """
    removed: set[str] = set()
    dirs_to_check: set[Path] = set()
    for path in orphan_files:
        try:
            path.unlink()
        except OSError as exc:
            print(f"WARNING: could not prune {path}: {exc}", file=sys.stderr)
            continue
        dirs_to_check.add(path.parent)
        removed.add(path.relative_to(mirror_dir).as_posix())
    for directory in sorted(dirs_to_check, key=lambda p: len(p.parts), reverse=True):
        current = directory
        while current != mirror_dir and current.is_dir() and not any(current.iterdir()):
            parent = current.parent
            current.rmdir()
            current = parent
    return removed


def load_manifest(path: Path) -> dict:
    """Load manifest YAML; return an empty default if the file is missing.

    A leading ``# ...`` comment line is preserved for re-emission; PyYAML
    parses only the body so the comment never lands inside the structure.
    """
    if not path.is_file():
        return {"materialized_at": None, "source": ".agents/skills", "files": []}
    text = path.read_text(encoding="utf-8")
    body_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    body = "\n".join(body_lines)
    data = yaml.safe_load(body) or {}
    data.setdefault("materialized_at", None)
    data.setdefault("source", ".agents/skills")
    data.setdefault("files", [])
    return data


def write_manifest(path: Path, manifest: dict) -> None:
    lines = [MANIFEST_HEADER.rstrip("\n")]
    lines.append(f"materialized_at: {manifest['materialized_at']}")
    lines.append(f"source: {manifest['source']}")
    lines.append("files:")
    for entry in manifest["files"]:
        lines.append(f"  - path: \"{entry['path']}\"")
        lines.append(f"    sha256: {entry['sha256']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def current_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_manifest_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    cleaned = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def materialize(
    source_dir: Path,
    mirror_dir: Path,
    manifest_path: Path,
    *,
    check: bool = False,
    dry_run: bool = False,
) -> int:
    skills = discover_skills(source_dir)
    if not skills:
        print(
            f"ERROR: no sdd-*/SKILL.md under {source_dir}",
            file=sys.stderr,
        )
        return 2

    drift = detect_drift(source_dir, mirror_dir, skills)
    manifest = load_manifest(manifest_path)
    mirror_orphans, manifest_orphan_paths = find_orphans(
        source_dir, mirror_dir, manifest_path, manifest, skills
    )
    orphan_paths = {p.relative_to(mirror_dir).as_posix() for p in mirror_orphans} | set(
        manifest_orphan_paths
    )
    total_drift = drift + len(orphan_paths)

    if check:
        if total_drift == 0:
            print(
                f"OK: no drift between {source_dir} and {mirror_dir} "
                f"(scope: portable families, excluding {', '.join(EXCLUDED_FAMILIES)})"
            )
            return 0
        print(
            f"DRIFT: {drift} file(s) differ or are missing, "
            f"{len(orphan_paths)} orphan path(s) in {mirror_dir}/manifest "
            f"with no counterpart in {source_dir}",
            file=sys.stderr,
        )
        return 1

    by_path = {entry["path"]: entry for entry in manifest["files"]}

    last_run = parse_manifest_timestamp(manifest.get("materialized_at"))
    if last_run is not None and last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    within_window = (
        last_run is not None
        and (now - last_run).total_seconds() < IDEMPOTENCY_WINDOW_SECONDS
    )
    skip_rewrite = total_drift == 0 and within_window

    if dry_run:
        for src in skills:
            rel = src.relative_to(source_dir).as_posix()
            dst = mirror_dir / rel
            if not dst.is_file():
                print(f"would create: {dst}")
            elif src.read_bytes() != dst.read_bytes():
                print(f"would update: {dst}")
            else:
                print(f"unchanged:    {dst}")
        for path in mirror_orphans:
            print(f"would prune:  {path}")
        for rel in manifest_orphan_paths:
            print(f"would drop from manifest: {rel}")
        if skip_rewrite:
            print(
                f"manifest within idempotency window "
                f"({IDEMPOTENCY_WINDOW_SECONDS}s); would not bump "
                f"materialized_at"
            )
        else:
            print(f"would update manifest: {manifest_path}")
        return 0

    files_written = 0
    new_entries: list[dict] = []
    for src in skills:
        rel_str = src.relative_to(source_dir).as_posix()
        dst = mirror_dir / rel_str
        data = src.read_bytes()
        existing_bytes = dst.read_bytes() if dst.is_file() else None
        if existing_bytes != data:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            files_written += 1
        sha = sha256_bytes(data)
        if rel_str in by_path:
            by_path[rel_str]["sha256"] = sha
        else:
            new_entries.append({"path": rel_str, "sha256": sha})

    mirror_orphan_rel = {p.relative_to(mirror_dir).as_posix() for p in mirror_orphans}
    removed_rel = prune_mirror_files(mirror_dir, mirror_orphans)
    failed_rel = mirror_orphan_rel - removed_rel
    files_pruned = len(removed_rel | (set(manifest_orphan_paths) - failed_rel))
    for rel in manifest_orphan_paths:
        if rel not in failed_rel:
            by_path.pop(rel, None)

    if skip_rewrite:
        print(
            f"OK: materialized {len(skills)} portable skills; no file changes; "
            f"manifest within idempotency window, materialized_at unchanged "
            f"({manifest['materialized_at']})"
        )
        return 0

    manifest["files"] = list(by_path.values()) + new_entries
    manifest["materialized_at"] = current_timestamp()
    write_manifest(manifest_path, manifest)

    verb = "updated" if files_written else "no file changes"
    prune_note = f"; pruned {files_pruned} orphan path(s)" if files_pruned else ""
    print(
        f"OK: materialized {len(skills)} portable skills; {verb}{prune_note}; "
        f"materialized_at={manifest['materialized_at']}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate .claude/skills/sdd-*/SKILL.md from .agents/skills/ "
            "and update the manifest."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check for drift; do not write. Exit 0 if no drift, 1 if drift.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing anything.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Override repo root (testing only).",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Override source dir (testing only). Defaults to <repo-root>/.agents/skills.",
    )
    parser.add_argument(
        "--mirror-dir",
        type=Path,
        default=None,
        help="Override mirror dir (testing only). Defaults to <repo-root>/.claude/skills.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Override manifest path (testing only). Defaults to <mirror-dir>/.claude-skills-manifest.yaml.",
    )
    args = parser.parse_args(argv)

    repo_root: Path = args.repo_root
    source_dir: Path = args.source_dir or repo_root / ".agents" / "skills"
    mirror_dir: Path = args.mirror_dir or repo_root / ".claude" / "skills"
    manifest_path: Path = args.manifest or mirror_dir / ".claude-skills-manifest.yaml"

    try:
        return materialize(
            source_dir,
            mirror_dir,
            manifest_path,
            check=args.check,
            dry_run=args.dry_run,
        )
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
