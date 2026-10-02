"""Unit 0002 — CA-01 (versión del kit declarada y legible) y CA-43
(unicidad: la cadena de versión no aparece en otros archivos de datos
del repo fuera del manifiesto)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = REPO_ROOT / "installer" / "kit_manifest.yaml"

sys.path.insert(0, str(REPO_ROOT))

from installer.manifest import get_kit_version  # noqa: E402

IGNORED_FOR_DUPLICATE_CHECK = (MANIFEST, Path(__file__).resolve())


def test_u0002_ca01_kit_version_is_non_empty_string() -> None:
    version = get_kit_version(MANIFEST)
    assert isinstance(version, str)
    assert version.strip() != ""


def test_u0002_ca01_kit_version_is_readable_via_get_kit_version() -> None:
    """`get_kit_version` no lanza sobre el manifiesto del repo."""
    version = get_kit_version(MANIFEST)
    assert version


def _repo_data_files() -> list[Path]:
    out: list[Path] = []
    # `railspec/` es otro producto con sus propias versiones de paquete: la de este
    # kit (`kit_version`) no es suya, aunque la cadena pueda coincidir.
    skip_dirs = {".git", "__pycache__", "node_modules", ".gitnexus", ".codebase-memory", "railspec"}
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.relative_to(REPO_ROOT).parts):
            continue
        if path in IGNORED_FOR_DUPLICATE_CHECK:
            continue
        if "/installer/tests/" in str(path):
            continue
        if "/tests/" in str(path) and "/.spec/" not in str(path):
            continue
        if "/.spec/scripts/tests/" in str(path):
            continue
        out.append(path)
    return out


def test_u0002_ca43_kit_version_appears_only_in_manifest() -> None:
    version = get_kit_version(MANIFEST)
    conflicts: list[str] = []
    for f in _repo_data_files():
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if version in text:
            if f.suffix in {".pyc", ".pyo"}:
                continue
            if f.suffix in {".json", ".jsonc"}:
                continue
            if f.suffix == ".md" and "install" not in f.name.lower():
                pass
            if "/.spec/units/" in str(f):
                continue
            conflicts.append(str(f.relative_to(REPO_ROOT)))
    assert not conflicts, (
        f"CA-43 — la cadena de versión aparece en otros archivos de datos: {conflicts}"
    )