"""Unit 0002 — CA-05, CA-06, CA-07, CA-12, CA-25, CA-26, CA-39, CA-41,
CA-48_verify, CA-49."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import yaml as yaml_mod

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = REPO_ROOT / "installer" / "kit_manifest.yaml"
CLI = REPO_ROOT / "installer" / "cli.py"

sys.path.insert(0, str(REPO_ROOT))

from installer.git_target import resolve_git_dir  # noqa: E402
from installer.manifest import load_and_validate, sha256_file  # noqa: E402
from installer.verifier import run_verify  # noqa: E402


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


def _pair_dest_index():
    entries = load_and_validate(MANIFEST)
    from verifier import iter_payload_files
    return iter_payload_files(entries)


def test_u0002_ca05_verify_with_missing_kit_version_returns_2(tmp_path: Path) -> None:
    target = tmp_path / "no-version"
    _git_init(target)
    broken = tmp_path / "no_version_manifest.yaml"
    body = MANIFEST.read_text(encoding="utf-8")
    lines = [line for line in body.splitlines() if "kit_version" not in line]
    broken.write_text("\n".join(lines), encoding="utf-8")
    code = run_verify(target, manifest_path=broken)
    assert code == 2


def test_u0002_ca06_verify_prints_both_kit_and_target_versions(tmp_path: Path) -> None:
    target = tmp_path / "verify-versions"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0, install.stdout + install.stderr
    verify = _run_cli(["--target", str(target)], cwd=REPO_ROOT)
    assert verify.returncode == 0
    out = verify.stdout
    assert "kit_version:" in out
    assert "destino_kit_version:" in out


def test_u0002_ca07_verify_on_legacy_target_says_no_recorded_version(tmp_path: Path) -> None:
    target = tmp_path / "legacy-no-record"
    _git_init(target)
    verify = _run_cli(["--target", str(target)], cwd=REPO_ROOT)
    assert "sin versión registrada" in verify.stdout


def test_u0002_ca12_verify_reports_three_categories(tmp_path: Path) -> None:
    target = tmp_path / "three-categories"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0, install.stdout + install.stderr

    pairs = _pair_dest_index()
    source_a, dest_a = pairs[0]
    source_b, dest_b = pairs[1]
    source_c, dest_c = pairs[2]

    (target / dest_a).write_bytes(b"diferente-de-baseline")
    (target / dest_b).write_bytes(source_b.read_bytes())
    (target / dest_c).unlink()

    verify = _run_cli(["--target", str(target)], cwd=REPO_ROOT)
    assert verify.returncode == 1, verify.stdout + verify.stderr
    out = verify.stdout
    has_drift = "drift" in out
    has_ausente = "ausente" in out
    has_desactualizada = "desactualizada" in out or dest_b not in out.splitlines() or not any(
        line.strip().endswith(dest_b) and ("drift" in line or "ausente" in line) for line in out.splitlines()
    )
    assert has_drift, out
    assert has_ausente, out


def test_u0002_ca12_verify_clean_target_returns_0(tmp_path: Path) -> None:
    target = tmp_path / "verify-clean"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    verify = _run_cli(["--target", str(target)], cwd=REPO_ROOT)
    assert verify.returncode == 0, verify.stdout + verify.stderr


def test_u0002_ca25_verify_names_orphan(tmp_path: Path) -> None:
    target = tmp_path / "orphan-named"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    pairs = _pair_dest_index()
    payload_paths = {dest for _, dest in pairs}
    orphan_rel = next(
        f"orphan/{i}.txt" for i in range(100) if f"orphan/{i}.txt" not in payload_paths
    )
    (target / orphan_rel).parent.mkdir(parents=True, exist_ok=True)
    (target / orphan_rel).write_bytes(b"orphan-content")
    import yaml as _y
    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    raw = record_path.read_text(encoding="utf-8")
    body = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))
    data = _y.safe_load(body)
    from manifest import sha256_bytes
    data["baseline"][orphan_rel] = sha256_bytes(b"orphan-content")
    new_lines = [
        "# sdd-kit install record",
        f'installed_at: "{data["installed_at"]}"',
        f'kit_version: "{data["kit_version"]}"',
        f'aggregate_digest: "{data["aggregate_digest"]}"',
        "baseline:",
    ]
    for k in sorted(data["baseline"].keys()):
        new_lines.append(f'  "{k}": "{data["baseline"][k]}"')
    record_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    verify = _run_cli(["--target", str(target)], cwd=REPO_ROOT)
    assert verify.returncode == 1, verify.stdout + verify.stderr
    assert "huérfana" in verify.stdout
    assert orphan_rel in verify.stdout


def test_u0002_ca26_verify_never_writes(tmp_path: Path) -> None:
    target = tmp_path / "verify-no-write"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    pairs = _pair_dest_index()
    source, dest = pairs[0]
    (target / dest).write_bytes(b"X")
    before = {p: (target / p).read_bytes() if (target / p).exists() else None for _, p in pairs}
    record_before = (resolve_git_dir(target) / "sdd-kit-install-record.yaml").read_bytes()
    _run_cli(["--target", str(target)], cwd=REPO_ROOT)
    after = {p: (target / p).read_bytes() if (target / p).exists() else None for _, p in pairs}
    record_after = (resolve_git_dir(target) / "sdd-kit-install-record.yaml").read_bytes()
    assert before == after
    assert record_before == record_after


def test_u0002_ca39_verify_returns_1_for_orphan_only(tmp_path: Path) -> None:
    target = tmp_path / "orphan-only"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    pairs = _pair_dest_index()
    payload_paths = {dest for _, dest in pairs}
    orphan_rel = next(
        f"orphan-only/{i}.txt" for i in range(100) if f"orphan-only/{i}.txt" not in payload_paths
    )
    (target / orphan_rel).parent.mkdir(parents=True, exist_ok=True)
    (target / orphan_rel).write_bytes(b"orphan-only-content")
    import yaml as _y
    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    raw = record_path.read_text(encoding="utf-8")
    body = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))
    data = _y.safe_load(body)
    from manifest import sha256_bytes
    data["baseline"][orphan_rel] = sha256_bytes(b"orphan-only-content")
    new_lines = [
        "# sdd-kit install record",
        f'installed_at: "{data["installed_at"]}"',
        f'kit_version: "{data["kit_version"]}"',
        f'aggregate_digest: "{data["aggregate_digest"]}"',
        "baseline:",
    ]
    for k in sorted(data["baseline"].keys()):
        new_lines.append(f'  "{k}": "{data["baseline"][k]}"')
    record_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    verify = _run_cli(["--target", str(target)], cwd=REPO_ROOT)
    assert verify.returncode == 1, verify.stdout + verify.stderr


def test_u0002_ca41_verify_skips_already_absent_orphan(tmp_path: Path) -> None:
    target = tmp_path / "orphan-already-absent"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    pairs = _pair_dest_index()
    payload_paths = {dest for _, dest in pairs}
    orphan_rel = next(
        f"orphan-abs/{i}.txt" for i in range(100) if f"orphan-abs/{i}.txt" not in payload_paths
    )
    import yaml as _y
    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    raw = record_path.read_text(encoding="utf-8")
    body = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))
    data = _y.safe_load(body)
    from manifest import sha256_bytes
    data["baseline"][orphan_rel] = sha256_bytes(b"orphan-abs-content")
    new_lines = [
        "# sdd-kit install record",
        f'installed_at: "{data["installed_at"]}"',
        f'kit_version: "{data["kit_version"]}"',
        f'aggregate_digest: "{data["aggregate_digest"]}"',
        "baseline:",
    ]
    for k in sorted(data["baseline"].keys()):
        new_lines.append(f'  "{k}": "{data["baseline"][k]}"')
    record_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    verify = _run_cli(["--target", str(target)], cwd=REPO_ROOT)
    out_lines = verify.stdout.splitlines()
    for line in out_lines:
        if orphan_rel in line:
            assert "huérfana" not in line, line


def test_u0002_ca48_verify_no_baseline_with_clean_target_returns_zero(tmp_path: Path) -> None:
    """Si el destino tiene todos los archivos de la carga pero el registro
    fue removido (clon nuevo sin instalar), la verificación contra la
    fuente debe pasar — no es drift, es un clon recién clonado."""
    target = tmp_path / "no-baseline-clean"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0, install.stdout + install.stderr
    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    record_path.unlink()
    verify = _run_cli(["--target", str(target)], cwd=REPO_ROOT)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "sin versión registrada" in verify.stdout


def test_u0002_ca49_verify_with_unreadable_record_returns_1(tmp_path: Path) -> None:
    target = tmp_path / "unreadable-record"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    record_path.write_text("garbage: [unterminated\n", encoding="utf-8")
    verify = _run_cli(["--target", str(target)], cwd=REPO_ROOT)
    assert verify.returncode == 1, verify.stdout + verify.stderr
    assert "ilegible" in verify.stdout or "ilegible" in verify.stderr