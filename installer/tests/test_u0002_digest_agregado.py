"""Unit 0002 — CA-50: si el contenido de la carga cambia sin que cambie
`kit_version`, el digest agregado de la carga cambia."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = REPO_ROOT / "installer" / "kit_manifest.yaml"
CLI = REPO_ROOT / "installer" / "cli.py"

sys.path.insert(0, str(REPO_ROOT))

from installer.git_target import resolve_git_dir  # noqa: E402
from installer.manifest import load_and_validate, sha256_bytes  # noqa: E402
from installer.verifier import iter_payload_files  # noqa: E402


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_u0002_ca50_aggregate_digest_changes_with_payload(tmp_path: Path) -> None:
    target = tmp_path / "aggregate"
    _git_init(target)
    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr

    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    raw = record_path.read_text(encoding="utf-8")
    digest_line = next(line for line in raw.splitlines() if line.startswith("aggregate_digest:"))
    aggregate_in_record = digest_line.split('"')[1]
    assert len(aggregate_in_record) == 64

    entries = load_and_validate(MANIFEST)
    pairs = iter_payload_files(entries)

    h = hashlib.sha256()
    for source_file, dest_rel in pairs:
        h.update(dest_rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(source_file.read_bytes())
        h.update(b"\x00")
    recomputed = h.hexdigest()
    assert recomputed == aggregate_in_record

    sample_source, _ = pairs[0]
    original = sample_source.read_bytes()
    flipped = sample_source.read_bytes() + b"x"
    try:
        sample_source.write_bytes(flipped)
        target2 = tmp_path / "aggregate-2"
        _git_init(target2)
        result2 = _run_cli(["--target", str(target2), "--install"], cwd=REPO_ROOT)
        assert result2.returncode == 0, result2.stdout + result2.stderr
        record_path2 = resolve_git_dir(target2) / "sdd-kit-install-record.yaml"
        raw2 = record_path2.read_text(encoding="utf-8")
        digest_line2 = next(line for line in raw2.splitlines() if line.startswith("aggregate_digest:"))
        aggregate2 = digest_line2.split('"')[1]
        assert aggregate2 != aggregate_in_record
    finally:
        sample_source.write_bytes(original)