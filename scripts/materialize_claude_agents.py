#!/usr/bin/env python3
"""Regenerate ``.claude/agents/`` from ``.agents/agents/`` + ``.spec/perfiles.yaml``.

Same pattern and contract as ``scripts/materialize_claude_skills.py``, but the
merge is three-way instead of a byte-for-byte copy: for each canonical
subagent body under ``.agents/agents/<rol>.md`` (frontmatter without
``model``/``effort``, kept by hand — same split as ``.agents/skills/`` vs.
``.claude/skills/``), this script fuses it with the (modelo, effort) pair the
`estandar` perfil declares for that role in ``.spec/perfiles.yaml``, and
writes the result to ``.claude/agents/<rol>.md`` with a generated-file header
comment right after the frontmatter. It also generates one variant file per
``(rol, effort)`` pair that ``effort_profile.required_variants`` reports —
today exactly ``sdd-implementador-xhigh.md`` — with a suffixed ``name``, a
one-line ``description``, and the same body as its canonical source.

Imports ``effort_profile`` (``.spec/scripts/``, stdlib-only) for
``load_profiles``/``required_variants`` instead of re-parsing
``perfiles.yaml`` — one reader of that file, shared across trees.

Exit codes: 0 OK (or no drift under ``--check``), 1 drift detected under
``--check`` (missing/differing mirror file, or orphan with no counterpart in
the perfil), 2 operational error (no canonical sources, missing canonical
body for a declared role, IO failure, or an invalid ``perfiles.yaml`` per
``effort_profile.ProfileError``).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DIR = REPO_ROOT / ".agents" / "agents"
DEFAULT_MIRROR_DIR = REPO_ROOT / ".claude" / "agents"
DEFAULT_MANIFEST = DEFAULT_MIRROR_DIR / ".claude-agents-manifest.yaml"
DEFAULT_PROFILES = REPO_ROOT / ".spec" / "perfiles.yaml"
MANIFEST_HEADER = "# GENERADO por scripts/materialize_claude_agents.py — no editar a mano.\n"
IDEMPOTENCY_WINDOW_SECONDS = 60

SPEC_SCRIPTS_DIR = REPO_ROOT / ".spec" / "scripts"
sys.path.insert(0, str(SPEC_SCRIPTS_DIR))
import effort_profile as ep  # noqa: E402


class MaterializeError(Exception):
    """Explicit operational failure — no silent fallback."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter body without delimiters, everything after the
    closing ``---``, delimiters excluded)."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise MaterializeError("frontmatter ausente o mal formado (falta '---' inicial)")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1 :])
    raise MaterializeError("frontmatter sin '---' de cierre")


def _insert_after_description(lines: list[str], extra: list[str]) -> tuple[list[str], bool]:
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.startswith("description:"):
            out.extend(extra)
            inserted = True
    return out, inserted


def build_canonical_frontmatter(frontmatter: str, modelo: object, effort: object) -> str:
    """Canonical frontmatter (no ``model``/``effort``) + that pair, inserted
    right after ``description:`` — same slot as the hand-authored files."""
    extra = [f"model: {modelo}"]
    if effort is not None:
        extra.append(f"effort: {effort}")
    lines, inserted = _insert_after_description(frontmatter.splitlines(), extra)
    if not inserted:
        lines.extend(extra)
    return "\n".join(lines) + "\n"


def build_variant_frontmatter(
    frontmatter: str, variant_name: str, description: str, modelo: object, effort: object
) -> str:
    """Same as ``build_canonical_frontmatter`` plus a suffixed ``name`` and a
    replaced one-line ``description``."""
    extra = [f"model: {modelo}"]
    if effort is not None:
        extra.append(f"effort: {effort}")
    out: list[str] = []
    desc_inserted = False
    for line in frontmatter.splitlines():
        if line.startswith("name:"):
            out.append(f"name: {variant_name}")
        elif line.startswith("description:"):
            out.append(f"description: {description}")
            out.extend(extra)
            desc_inserted = True
        else:
            out.append(line)
    if not desc_inserted:
        out.append(f"description: {description}")
        out.extend(extra)
    return "\n".join(out) + "\n"


def _header_comment(source_role: str) -> str:
    return (
        f"<!-- generado por scripts/materialize_claude_agents.py desde "
        f".spec/perfiles.yaml y .agents/agents/{source_role}.md — no editar a mano -->"
    )


def _assemble(frontmatter: str, source_role: str, body: str) -> str:
    return f"---\n{frontmatter}---\n{_header_comment(source_role)}\n{body}"


def discover_canonical_bodies(source_dir: Path) -> dict[str, Path]:
    """``{rol: path}`` for every ``<rol>.md`` directly under ``source_dir``."""
    if not source_dir.is_dir():
        return {}
    return {p.stem: p for p in sorted(source_dir.glob("*.md"))}


def compute_expected(source_dir: Path, profiles: dict[str, object]) -> dict[str, bytes]:
    """``{relative filename: expected bytes}`` for the canonical roles' base
    files plus every variant ``perfiles.yaml`` requires."""
    bodies = discover_canonical_bodies(source_dir)
    expected: dict[str, bytes] = {}

    estandar_roles = profiles["perfiles"]["estandar"]["roles"]
    for role in ep.KNOWN_ROLES:
        entry = estandar_roles.get(role, {})
        path = bodies.get(role)
        if path is None:
            raise MaterializeError(f"falta el cuerpo canónico {source_dir / (role + '.md')}")
        frontmatter, body = split_frontmatter(path.read_text(encoding="utf-8"))
        merged_fm = build_canonical_frontmatter(frontmatter, entry.get("modelo"), entry.get("effort"))
        expected[f"{role}.md"] = _assemble(merged_fm, role, body).encode("utf-8")

    for role, effort, modelo in sorted(ep.required_variants(profiles)):
        path = bodies.get(role)
        if path is None:
            raise MaterializeError(
                f"falta el cuerpo canónico {source_dir / (role + '.md')} "
                f"requerido por la variante {role}-{effort}"
            )
        frontmatter, body = split_frontmatter(path.read_text(encoding="utf-8"))
        variant_name = f"{role}-{effort}"
        description = f"Variante {effort} de {role}."
        merged_fm = build_variant_frontmatter(frontmatter, variant_name, description, modelo, effort)
        expected[f"{variant_name}.md"] = _assemble(merged_fm, role, body).encode("utf-8")

    return expected


def detect_drift(mirror_dir: Path, expected: dict[str, bytes]) -> int:
    drift = 0
    for rel, data in expected.items():
        dst = mirror_dir / rel
        if not dst.is_file() or dst.read_bytes() != data:
            drift += 1
    return drift


def find_orphans(mirror_dir: Path, manifest_path: Path, manifest: dict, expected: dict[str, bytes]) -> tuple[list[Path], list[str]]:
    """Mirror files/manifest entries matching ``sdd-*.md`` with no counterpart
    in ``expected`` — a canonical role dropped, or a variant no longer
    required."""
    mirror_orphans: list[Path] = []
    if mirror_dir.is_dir():
        for path in sorted(mirror_dir.glob("sdd-*.md")):
            if path.is_file() and path.name not in expected:
                mirror_orphans.append(path)
    manifest_orphans = [
        entry["path"] for entry in manifest.get("files", [])
        if entry["path"].startswith("sdd-") and entry["path"] not in expected
    ]
    return mirror_orphans, manifest_orphans


def load_manifest(path: Path) -> dict:
    if not path.is_file():
        return {"materialized_at": None, "source": ".agents/agents", "files": []}
    text = path.read_text(encoding="utf-8")
    body_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    data = yaml.safe_load("\n".join(body_lines)) or {}
    data.setdefault("materialized_at", None)
    data.setdefault("source", ".agents/agents")
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
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def materialize(
    source_dir: Path,
    mirror_dir: Path,
    manifest_path: Path,
    profiles_path: Path,
    *,
    check: bool = False,
    dry_run: bool = False,
) -> int:
    try:
        profiles = ep.load_profiles(profiles_path)
        expected = compute_expected(source_dir, profiles)
    except (ep.ProfileError, MaterializeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not expected:
        print(f"ERROR: no sdd-*.md canonical bodies under {source_dir}", file=sys.stderr)
        return 2

    drift = detect_drift(mirror_dir, expected)
    manifest = load_manifest(manifest_path)
    mirror_orphans, manifest_orphan_paths = find_orphans(mirror_dir, manifest_path, manifest, expected)
    orphan_paths = {p.name for p in mirror_orphans} | set(manifest_orphan_paths)
    total_drift = drift + len(orphan_paths)

    if check:
        if total_drift == 0:
            print(f"OK: no drift between {source_dir}+perfiles.yaml and {mirror_dir}")
            return 0
        print(
            f"DRIFT: {drift} file(s) differ or are missing, "
            f"{len(orphan_paths)} orphan path(s) in {mirror_dir}/manifest",
            file=sys.stderr,
        )
        return 1

    by_path = {entry["path"]: entry for entry in manifest["files"]}
    last_run = parse_manifest_timestamp(manifest.get("materialized_at"))
    now = datetime.now(timezone.utc)
    within_window = last_run is not None and (now - last_run).total_seconds() < IDEMPOTENCY_WINDOW_SECONDS
    skip_rewrite = total_drift == 0 and within_window

    if dry_run:
        for rel, data in expected.items():
            dst = mirror_dir / rel
            if not dst.is_file():
                print(f"would create: {dst}")
            elif dst.read_bytes() != data:
                print(f"would update: {dst}")
            else:
                print(f"unchanged:    {dst}")
        for path in mirror_orphans:
            print(f"would prune:  {path}")
        for rel in manifest_orphan_paths:
            print(f"would drop from manifest: {rel}")
        return 0

    files_written = 0
    new_entries: list[dict] = []
    for rel, data in expected.items():
        dst = mirror_dir / rel
        existing = dst.read_bytes() if dst.is_file() else None
        if existing != data:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            files_written += 1
        sha = sha256_bytes(data)
        if rel in by_path:
            by_path[rel]["sha256"] = sha
        else:
            new_entries.append({"path": rel, "sha256": sha})

    files_pruned = 0
    for path in mirror_orphans:
        try:
            path.unlink()
            files_pruned += 1
        except OSError as exc:
            print(f"WARNING: could not prune {path}: {exc}", file=sys.stderr)
    for rel in manifest_orphan_paths:
        by_path.pop(rel, None)

    if skip_rewrite:
        print(
            f"OK: materialized {len(expected)} agent files; no file changes; "
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
        f"OK: materialized {len(expected)} agent files; {verb}{prune_note}; "
        f"materialized_at={manifest['materialized_at']}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate .claude/agents/sdd-*.md (base roles + variants) from "
            ".agents/agents/ + .spec/perfiles.yaml."
        )
    )
    parser.add_argument("--check", action="store_true", help="Only check for drift; exit 0/1.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="Override repo root (testing only).")
    parser.add_argument("--source-dir", type=Path, default=None, help="Override source dir (testing only).")
    parser.add_argument("--mirror-dir", type=Path, default=None, help="Override mirror dir (testing only).")
    parser.add_argument("--manifest", type=Path, default=None, help="Override manifest path (testing only).")
    parser.add_argument("--profiles", type=Path, default=None, help="Override perfiles.yaml path (testing only).")
    args = parser.parse_args(argv)

    repo_root: Path = args.repo_root
    source_dir: Path = args.source_dir or repo_root / ".agents" / "agents"
    mirror_dir: Path = args.mirror_dir or repo_root / ".claude" / "agents"
    manifest_path: Path = args.manifest or mirror_dir / ".claude-agents-manifest.yaml"
    profiles_path: Path = args.profiles or repo_root / ".spec" / "perfiles.yaml"

    try:
        return materialize(
            source_dir, mirror_dir, manifest_path, profiles_path,
            check=args.check, dry_run=args.dry_run,
        )
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
