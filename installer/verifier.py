"""sdd-kit's verify operation: compares the target's content against this
repository's own sources for every manifest entry, and writes nothing.

Exit 0 when every entry is byte-identical between target and source, exit 1
when at least one differs or is missing, exit 2 on a manifest problem
(unreadable/invalid, or an entry whose source does not exist here).
"""

from __future__ import annotations

import sys
from pathlib import Path

from manifest import DEFAULT_MANIFEST_PATH, REPO_ROOT, ManifestEntry, ManifestError, load_and_validate

EXIT_OK = 0
EXIT_DIVERGENT = 1
EXIT_OPERATIONAL_ERROR = 2


def iter_payload_files(
    entries: list[ManifestEntry], repo_root: Path = REPO_ROOT
) -> list[tuple[Path, str]]:
    """Expand each manifest entry into ``(source_file, dest_rel)`` pairs.

    A directory entry (e.g. ``.agents/skills``) expands to every file under
    it, recursively, with its destination path prefixed accordingly. A file
    entry yields itself unchanged. Shared by the verify and install
    operations so both classify content the same way."""
    pairs: list[tuple[Path, str]] = []
    for entry in entries:
        source_path = repo_root / entry.source
        if source_path.is_dir():
            dest_prefix = entry.dest.rstrip("/")
            for file_path in sorted(source_path.rglob("*")):
                if not file_path.is_file():
                    continue
                rel = file_path.relative_to(source_path).as_posix()
                pairs.append((file_path, f"{dest_prefix}/{rel}"))
        else:
            pairs.append((source_path, entry.dest))
    return pairs


def find_divergent(pairs: list[tuple[Path, str]], target: Path) -> list[str]:
    """Return the dest-relative paths present in ``pairs`` that are missing
    from ``target`` or whose content differs from their source."""
    divergent: list[str] = []
    for source_file, dest_rel in pairs:
        dest_path = target / dest_rel
        if not dest_path.is_file():
            divergent.append(dest_rel)
            continue
        if dest_path.read_bytes() != source_file.read_bytes():
            divergent.append(dest_rel)
    return divergent


def run_verify(target: Path, *, manifest_path: Path = DEFAULT_MANIFEST_PATH) -> int:
    try:
        entries = load_and_validate(manifest_path)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL_ERROR

    pairs = iter_payload_files(entries)
    divergent = find_divergent(pairs, target)

    if divergent:
        for rel in divergent:
            print(rel)
        return EXIT_DIVERGENT

    print(f"OK: sin divergencias ({len(pairs)} archivo(s) verificados)")
    return EXIT_OK
