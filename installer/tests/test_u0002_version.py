"""Unit 0002 — CA-03: tras `--install` exitoso, el registro contiene
`kit_version` igual al valor declarado en `kit_manifest.yaml`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = REPO_ROOT / "installer" / "kit_manifest.yaml"
CLI = REPO_ROOT / "installer" / "cli.py"

sys.path.insert(0, str(REPO_ROOT))

from installer.git_target import resolve_git_dir  # noqa: E402
from installer.manifest import get_kit_version, read_install_record  # noqa: E402


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


def test_u0002_ca03_install_record_persists_kit_version(tmp_path: Path) -> None:
    target = tmp_path / "version-record"
    _git_init(target)
    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr

    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    record = read_install_record(record_path)
    assert record.get("kit_version") == get_kit_version(MANIFEST)