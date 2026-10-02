"""Unit 0002 — CA-08 (línea base: tras `--install` exitoso, una entrada por
cada ruta de `iter_payload_files(entries)` con el digest recalculado del
archivo del destino)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = REPO_ROOT / "installer" / "kit_manifest.yaml"
CLI = REPO_ROOT / "installer" / "cli.py"

sys.path.insert(0, str(REPO_ROOT))

from installer.convivencia import digest_de_instalacion  # noqa: E402
from installer.git_target import resolve_git_dir  # noqa: E402
from installer.manifest import load_and_validate  # noqa: E402
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


def test_u0002_ca08_baseline_has_one_entry_per_payload_path(tmp_path: Path) -> None:
    target = tmp_path / "baseline-coverage"
    _git_init(target)
    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr

    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    raw = record_path.read_text(encoding="utf-8")
    body = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))
    data = yaml.safe_load(body)
    baseline = data["baseline"]

    entries = load_and_validate(MANIFEST)
    pairs = iter_payload_files(entries)
    payload_dests = {dest for _, dest in pairs}

    assert set(baseline.keys()) == payload_dests

    # La línea base de cada ruta es el digest de su parte del kit: el del archivo entero,
    # salvo en los cuatro compartidos con Railspec (ver `installer/convivencia.py`).
    for source_file, dest_rel in pairs:
        actual = digest_de_instalacion(dest_rel, (target / dest_rel).read_bytes())
        assert baseline[dest_rel] == actual, dest_rel