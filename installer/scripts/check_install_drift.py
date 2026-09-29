#!/usr/bin/env python3
"""Chequeo de drift de instalación para el hook `pre-push` (CA-33, CA-48).

Recorre la línea base persistida en `<git_dir>/sdd-kit-install-record.yaml`
y compara, ruta por ruta, el digest SHA-256 calculado sobre los bytes del
archivo en el worktree contra el digest registrado. Si alguna ruta difiere,
la imprime y sale con código distinto de 0 — el hook de pre-push propaga
ese código al push y lo rechaza.

Sin línea base (registro ausente o ilegible) el script sale con código 0
y una línea informativa: un clon nuevo no puede quedar impedido de trabajar
porque la línea base aún no existe (CA-48).

Dependencia: `pyyaml` (la misma que ya usa `installer/manifest.py`). El script
viaja como entrada de `installer/kit_manifest.yaml`, así que está disponible
en el worktree del destino; el hook `pre-push` lo invoca por ruta absoluta
desde el worktree (`$(git rev-parse --show-toplevel)/installer/scripts/check_install_drift.py`).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

INSTALL_RECORD_NAME = "sdd-kit-install-record.yaml"
SKIP_MESSAGE = "sin línea base"


def _git_dir() -> Path:
    raw = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        capture_output=True,
        text=True,
    )
    if raw.returncode != 0:
        print(f"check_install_drift: git rev-parse falló: {raw.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return Path(raw.stdout.strip())


def _read_yaml_simple(path: Path) -> dict | None:
    try:
        import yaml
    except ImportError:
        print("check_install_drift: pyyaml no disponible", file=sys.stderr)
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"check_install_drift: no se pudo leer {path}: {exc}", file=sys.stderr)
        return None
    body_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    try:
        data = yaml.safe_load("\n".join(body_lines))
    except yaml.YAMLError as exc:
        print(f"check_install_drift: registro ilegible ({exc}) — {SKIP_MESSAGE}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        return None
    return data


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def main() -> int:
    try:
        git_dir = _git_dir()
    except Exception:
        return 1
    record_path = git_dir / INSTALL_RECORD_NAME
    if not record_path.is_file():
        print(f"check_install_drift: {SKIP_MESSAGE} (no existe {record_path})")
        return 0

    data = _read_yaml_simple(record_path)
    if data is None:
        return 0

    baseline = data.get("baseline") or {}
    if not isinstance(baseline, dict):
        print(f"check_install_drift: registro con baseline inválido — {SKIP_MESSAGE}", file=sys.stderr)
        return 0

    worktree = git_dir.parent
    drift_paths: list[str] = []
    for rel, expected in baseline.items():
        if not isinstance(rel, str) or not isinstance(expected, str):
            continue
        target = (worktree / rel).resolve()
        try:
            target.relative_to(worktree.resolve())
        except ValueError:
            drift_paths.append(rel)
            continue
        if not target.is_file():
            drift_paths.append(rel)
            continue
        actual = _sha256_file(target)
        if actual is None or actual != expected:
            drift_paths.append(rel)

    if not drift_paths:
        return 0

    print(
        json.dumps(
            {"drift_paths": drift_paths},
            ensure_ascii=False,
        )
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())