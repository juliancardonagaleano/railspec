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
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

INSTALL_RECORD_NAME = "sdd-kit-install-record.yaml"
SKIP_MESSAGE = "sin línea base"

# --- Convivencia con Railspec ----------------------------------------------
# Copia de `installer/convivencia.py` (este script viaja solo al destino y no
# puede importar el paquete `installer`); `test_convivencia_railspec.py` prueba
# que las dos dan el mismo digest. En `.mcp.json`, `opencode.jsonc`,
# `.claude/settings.json` y `AGENTS.md` el digest de línea base es el de la
# parte del kit: lo que añade `railspec instalar` no es drift.
_MEZCLADOS = {
    ".mcp.json": (("mcpServers",),),
    "opencode.jsonc": (("mcp",), ("agent",)),
    ".claude/settings.json": (("hooks", "PreToolUse"), ("hooks", "PrePush")),
}
_BLOQUE_RAILSPEC = re.compile(rb"<!-- railspec:inicio[^>]*-->.*?<!-- railspec:fin -->\n?", re.DOTALL)
_COMENTARIOS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.DOTALL)
_COMA_FINAL = re.compile(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])', re.DOTALL)


def _parte_del_kit(rel: str, data: bytes) -> bytes | None:
    try:
        texto = data.decode("utf-8")
        texto = _COMENTARIOS.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", texto)
        texto = _COMA_FINAL.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(0), texto)
        documento: Any = json.loads(texto) if texto.strip() else {}
    except ValueError:
        return None
    if not isinstance(documento, dict):
        return None
    parte: dict[str, Any] = {}
    for claves in _MEZCLADOS[rel]:
        actual: Any = documento
        for clave in claves:
            actual = actual.get(clave) if isinstance(actual, dict) else None
        marcada = lambda e: isinstance(e, dict) and e.get("_sdd_kit") is True  # noqa: E731
        if isinstance(actual, dict):
            entradas: Any = {k: v for k, v in actual.items() if marcada(v)}
        elif isinstance(actual, list):
            entradas = [e for e in actual if marcada(e)]
        else:
            continue
        if entradas:
            parte[".".join(claves)] = entradas
    return json.dumps(parte, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _contenido_del_kit(rel: str, data: bytes) -> bytes:
    if rel in _MEZCLADOS:
        parte = _parte_del_kit(rel, data)
        return data if parte is None else parte
    if rel == "AGENTS.md":
        encontrado = _BLOQUE_RAILSPEC.search(data)
        if encontrado is None:
            return data
        antes, despues = data[: encontrado.start()], data[encontrado.end() :]
        return (antes[:-1] if antes.endswith(b"\n\n") else antes) + despues
    return data


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


def _coincide(rel: str, path: Path, expected: str) -> bool:
    """El archivo es lo que registra ``expected``: por su parte del kit o, en registros
    anteriores, entero."""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return expected in (
        hashlib.sha256(_contenido_del_kit(rel, data)).hexdigest(),
        hashlib.sha256(data).hexdigest(),
    )


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
        if not _coincide(rel, target, expected):
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