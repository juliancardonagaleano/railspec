#!/usr/bin/env python3
"""Regenerate ``.claude/commands/`` from ``.agents/commands/``.

Originally ``scripts/materialize_claude_commands.py``; moved verbatim to
``installer/materializers/commands.py`` (unit 0006) so that
``installer/cli.py materialize commands`` and the legacy shim at
``scripts/materialize_claude_commands.py`` both reach the same ``main`` —
see CA-01, CA-03, CA-04.

Copies every ``.agents/commands/*.md`` file byte-for-byte to its mirror under
``.claude/commands/<name>.md``, prepending a generated-header comment line
(``<!-- generado por installer/materializers/commands.py desde
.agents/commands/<name>.md -- no editar a mano -->``) that the canonical
source itself never carries, recomputes SHA-256 for each mirrored file and
updates ``.claude/commands/.claude-commands-manifest.yaml`` so every entry's
SHA matches the regenerated mirror and ``materialized_at`` reflects the most
recent run.

Same pattern as ``materialize_claude_skills.py`` (copy byte-for-byte + a
SHA-256 manifest), not the frontmatter-fusion pattern of
``materialize_claude_agents.py``: commands carry no per-(role, effort)
variants, so there is nothing to fuse.

Pruning: any mirror file or manifest entry that no longer has a counterpart
under ``.agents/commands/`` (because the source file was removed or renamed)
is deleted from the mirror and dropped from the manifest. This is symmetric
with drift detection above -- drift finds what the source has and the mirror
lacks or has stale; pruning finds what the mirror has and the source no
longer has. ``--check`` reports an orphan the same way it reports drift:
exit 1 until the mirror/manifest are pruned.

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

from .agents import MaterializeError, split_frontmatter  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR = REPO_ROOT / ".agents" / "commands"
DEFAULT_MIRROR_DIR = REPO_ROOT / ".claude" / "commands"
DEFAULT_MANIFEST = DEFAULT_MIRROR_DIR / ".claude-commands-manifest.yaml"
MANIFEST_HEADER = "# GENERADO por installer/materializers/commands.py — no editar a mano.\n"
IDEMPOTENCY_WINDOW_SECONDS = 60


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def generated_header(rel_str: str) -> str:
    return (
        f"<!-- generado por installer/materializers/commands.py desde "
        f".agents/commands/{rel_str} — no editar a mano -->\n"
    )


def rendered_mirror_bytes(source_bytes: bytes, rel_str: str) -> bytes:
    """Insert the generated-header comment right after the closing '---' of
    the frontmatter, never before the opening '---' — a header prepended to
    the raw bytes would push the frontmatter off line 1 and break any parser
    that expects YAML frontmatter to open the file. Reuses
    ``materialize_claude_agents.split_frontmatter`` (same parsing rule, same
    error messages) instead of a second implementation of the same split —
    both scripts live in ``scripts/``, outside the self-contained ``sdd-kit/``
    tree, so importing between them here does not reintroduce a dependency
    on anything outside this repository."""
    text = source_bytes.decode("utf-8")
    frontmatter_body, body = split_frontmatter(text)
    return f"---\n{frontmatter_body}---\n{generated_header(rel_str)}{body}".encode("utf-8")


def discover_commands(source_dir: Path) -> list[Path]:
    """Return every ``*.md`` file directly under ``source_dir``, sorted."""
    if not source_dir.is_dir():
        return []
    return sorted(p for p in source_dir.iterdir() if p.is_file() and p.suffix == ".md")


def detect_drift(source_dir: Path, mirror_dir: Path, commands: list[Path]) -> int:
    """Count missing or differing mirror files among ``commands``."""
    drift = 0
    for src in commands:
        rel = src.relative_to(source_dir)
        dst = mirror_dir / rel
        expected = rendered_mirror_bytes(src.read_bytes(), rel.as_posix())
        if not dst.is_file():
            drift += 1
            continue
        if dst.read_bytes() != expected:
            drift += 1
    return drift


def find_orphans(
    source_dir: Path,
    mirror_dir: Path,
    manifest_path: Path,
    manifest: dict,
    commands: list[Path],
) -> tuple[list[Path], list[str]]:
    """Return mirror files and manifest entries with no counterpart in source."""
    valid_rel = {src.relative_to(source_dir).as_posix() for src in commands}

    def is_prunable(rel: str) -> bool:
        return rel not in valid_rel

    mirror_orphan_files: list[Path] = []
    if mirror_dir.is_dir():
        for path in sorted(mirror_dir.iterdir()):
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
    """Delete orphan mirror files. Returns the set of relative paths removed."""
    removed: set[str] = set()
    for path in orphan_files:
        try:
            path.unlink()
        except OSError as exc:
            print(f"WARNING: could not prune {path}: {exc}", file=sys.stderr)
            continue
        removed.add(path.relative_to(mirror_dir).as_posix())
    return removed


def load_manifest(path: Path) -> dict:
    """Load manifest YAML; return an empty default if the file is missing."""
    if not path.is_file():
        return {"materialized_at": None, "source": ".agents/commands", "files": []}
    text = path.read_text(encoding="utf-8")
    body_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    body = "\n".join(body_lines)
    data = yaml.safe_load(body) or {}
    data.setdefault("materialized_at", None)
    data.setdefault("source", ".agents/commands")
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
    commands = discover_commands(source_dir)
    if not commands:
        print(
            f"ERROR: no *.md under {source_dir}",
            file=sys.stderr,
        )
        return 2

    drift = detect_drift(source_dir, mirror_dir, commands)
    manifest = load_manifest(manifest_path)
    mirror_orphans, manifest_orphan_paths = find_orphans(
        source_dir, mirror_dir, manifest_path, manifest, commands
    )
    orphan_paths = {p.relative_to(mirror_dir).as_posix() for p in mirror_orphans} | set(
        manifest_orphan_paths
    )
    total_drift = drift + len(orphan_paths)

    if check:
        if total_drift == 0:
            print(f"OK: no drift between {source_dir} and {mirror_dir}")
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
        for src in commands:
            rel = src.relative_to(source_dir).as_posix()
            dst = mirror_dir / rel
            expected = rendered_mirror_bytes(src.read_bytes(), rel)
            if not dst.is_file():
                print(f"would create: {dst}")
            elif dst.read_bytes() != expected:
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
    for src in commands:
        rel_str = src.relative_to(source_dir).as_posix()
        dst = mirror_dir / rel_str
        data = rendered_mirror_bytes(src.read_bytes(), rel_str)
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
            f"OK: materialized {len(commands)} commands; no file changes; "
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
        f"OK: materialized {len(commands)} commands; {verb}{prune_note}; "
        f"materialized_at={manifest['materialized_at']}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate .claude/commands/*.md from .agents/commands/ "
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
        help="Override source dir (testing only). Defaults to <repo-root>/.agents/commands.",
    )
    parser.add_argument(
        "--mirror-dir",
        type=Path,
        default=None,
        help="Override mirror dir (testing only). Defaults to <repo-root>/.claude/commands.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Override manifest path (testing only). Defaults to <mirror-dir>/.claude-commands-manifest.yaml.",
    )
    args = parser.parse_args(argv)

    repo_root: Path = args.repo_root
    source_dir: Path = args.source_dir or repo_root / ".agents" / "commands"
    mirror_dir: Path = args.mirror_dir or repo_root / ".claude" / "commands"
    manifest_path: Path = args.manifest or mirror_dir / ".claude-commands-manifest.yaml"

    try:
        return materialize(
            source_dir,
            mirror_dir,
            manifest_path,
            check=args.check,
            dry_run=args.dry_run,
        )
    except (OSError, MaterializeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
