"""Load and validate the kit manifest (``installer/kit_manifest.yaml``).

The manifest declares the payload of the kit: a list of entries, each one a
pair of (source path in this repository, destination path relative to the
install target). This module only reads and validates that data — it never
writes anything and never touches an install target.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST_PATH = REPO_ROOT / "installer" / "kit_manifest.yaml"


class ManifestError(Exception):
    """The manifest is missing, unreadable, malformed, or declares an entry
    whose source does not exist in this repository. Callers that need exit
    code 2 semantics catch this and translate it."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


@dataclass(frozen=True)
class ManifestEntry:
    source: str
    dest: str


def _read_yaml_body(path: Path) -> dict:
    """Parse ``path`` as YAML, skipping leading ``#``-comment lines the same
    way ``scripts/materialize_claude_agents.py:load_manifest`` does."""
    text = path.read_text(encoding="utf-8")
    body_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    try:
        data = yaml.safe_load("\n".join(body_lines))
    except yaml.YAMLError as exc:
        raise ManifestError(f"manifiesto ilegible: {exc}") from exc
    if data is None:
        raise ManifestError("manifiesto vacío")
    if not isinstance(data, dict):
        raise ManifestError("manifiesto mal formado: se esperaba un mapeo en la raíz")
    return data


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> list[ManifestEntry]:
    """Parse ``path`` and return its entries. Raises ``ManifestError`` on any
    parse or shape problem — missing file, invalid YAML, missing ``entries``,
    or an entry with an empty ``source``/``dest``. Does not check that the
    sources exist; that is ``validate_entries``."""
    if not path.is_file():
        raise ManifestError(f"manifiesto no encontrado: {path}")
    data = _read_yaml_body(path)
    raw_entries = data.get("entries")
    if raw_entries is None:
        raise ManifestError("manifiesto sin clave 'entries'")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ManifestError("manifiesto sin entradas: 'entries' debe ser una lista no vacía")

    entries: list[ManifestEntry] = []
    for i, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise ManifestError(f"entrada {i} mal formada: se esperaba un mapeo")
        source = raw.get("source")
        dest = raw.get("dest")
        if not isinstance(source, str) or not source.strip():
            raise ManifestError(f"entrada {i} sin ruta de origen no vacía")
        if not isinstance(dest, str) or not dest.strip():
            raise ManifestError(f"entrada {i} sin ruta de destino no vacía")
        entries.append(ManifestEntry(source=source, dest=dest))
    return entries


def validate_entries(entries: list[ManifestEntry], repo_root: Path = REPO_ROOT) -> None:
    """Raise ``ManifestError`` naming the first entry whose source does not
    exist under ``repo_root``. Called before any write to the install
    target — a missing source stops the run with no effects."""
    for entry in entries:
        source_path = repo_root / entry.source
        if not source_path.exists():
            raise ManifestError(f"la ruta de origen no existe: {entry.source}")


def load_and_validate(
    path: Path = DEFAULT_MANIFEST_PATH, repo_root: Path = REPO_ROOT
) -> list[ManifestEntry]:
    """``load_manifest`` followed by ``validate_entries`` — the check every
    caller that is about to act on the manifest should run."""
    entries = load_manifest(path)
    validate_entries(entries, repo_root)
    return entries
