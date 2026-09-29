#!/usr/bin/env python3
"""Auditor de superficie de gobernanza del kit (unit 0003, fix 7).

Tres verificaciones contra el filesystem de un destino (o del kit sobre sí
mismo cuando se invoca sin `--unit`):
  (a) `configuration_contract:` del spec declara una lista de rutas raíz.
      Cada ruta debe existir (CA-01, CA-02).
  (b) Cualquier archivo que declare `mcpServers`, `mcp` o `pce-mcp` y NO
      esté en la lista declarativa causa `superficie-no-declarada` (CA-03,
      CA-07).
  (c) `fs_hash` del filesystem se calcula con un algoritmo determinista
      (excluye `.git/`, `.spec/`, `.gitnexus/`), para que el gate pueda
      detectar cambios entre fases (CA-04, CA-13).

Funciones puras (testeables con `tmp_path` sin I/O fuera del argumento) +
CLI que las orquesta. Sin fallback a memoria: si la spec no tiene
`configuration_contract:`, el chequeo se reduce al fallback del skill
(`sdd-gate/SKILL.md:95`).

Usage:
    python3 .spec/scripts/check_governance_surface.py \\
        --unit <ruta-unidad> --spec <ruta-spec> --filesystem-root <ruta>

Exit codes:
    0   superficie consistente
    1   superficie no declarada (algún archivo declara MCP/gob fuera de la lista)
    2   configuration_contract inconsistente (ruta declarada ausente)
    3   error operativo (spec no parseable, filesystem inaccesible, etc.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

EXCLUDED_DIRS = (".git", ".spec", ".gitnexus", ".venv", ".pytest_cache", ".ruff_cache", ".tmp", ".codebase-memory")

# Regex de las dos líneas que `## Tareas` y `## Bitácora` etc. usan como
# nivel-2; `## ` al inicio de línea es la convención. Aquí solo nos interesa
# un `configuration_contract:` declarado en el spec.
CONFIG_CONTRACT_RE = re.compile(
    r"^configuration_contract:\s*\[(?P<paths>[^\]]*)\]",
    re.MULTILINE,
)
# Detección de "este archivo DECLARA servidores MCP", no "este archivo
# MENCIONA pce-mcp en prosa". Las regex son estrictas: exigen la forma
# estructural de un JSON config (clave `"mcpServers"` o clave `"mcp"`), no
# la palabra suelta en cualquier contexto. Esto evita falsos positivos
# en prosa como `AGENTS.md` (que menciona pce-mcp en § Gobernanza pero no
# declara servidores).
DECLARED_GOVERNANCE_JSON_RE = re.compile(
    r'"mcpServers"\s*:\s*\{|"mcp"\s*:\s*\{',
    re.MULTILINE,
)
PATH_TOKEN_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'|(\S+)')


@dataclass
class Report:
    verdict: str  # "ok" | "superficie-no-declarada" | "configuration_contract-inconsistente" | "error"
    causes: list[str] = field(default_factory=list)
    declared: list[str] = field(default_factory=list)
    found: list[str] = field(default_factory=list)
    fs_hash: str = ""
    filesystem_root: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "causes": list(self.causes),
            "declared": list(self.declared),
            "found": list(self.found),
            "fs_hash": self.fs_hash,
            "filesystem_root": self.filesystem_root,
            "notes": list(self.notes),
        }

    def exit_code(self) -> int:
        return {
            "ok": 0,
            "superficie-no-declarada": 1,
            "configuration_contract-inconsistente": 2,
            "error": 3,
        }.get(self.verdict, 3)


def read_configuration_contract(spec_path: Path) -> list[str]:
    """Lee `configuration_contract: [...]` del spec y devuelve las rutas.

    El spec puede declarar el contrato de dos formas (compatible hacia atrás):
      - Bloque `configuration_contract: [".mcp.json", "opencode.jsonc", ...]`
        (lista plana de strings separados por comas, opcionalmente entre comillas).
      - Bloque markdown bajo un heading `## Configuration contract` (futuro).

    Por ahora solo se soporta la forma plana. Si el spec no tiene el bloque,
    se devuelve una lista vacía (el skill decide si cae al fallback).
    """
    text = spec_path.read_text(encoding="utf-8")
    m = CONFIG_CONTRACT_RE.search(text)
    if not m:
        return []
    inner = m.group("paths")
    paths = []
    for piece in inner.split(","):
        p = piece.strip().strip('"').strip("'")
        if p:
            paths.append(p)
    return paths


def find_declared_governance(filesystem_root: Path) -> list[str]:
    """Busca archivos que DECLAREN servidores MCP (no que los mencionen).

    Criterio: cualquier archivo JSON/JSONC bajo la raíz (incluyendo
    dotfiles como `.mcp.json` o `.opencode/opencode.jsonc`) cuyo contenido
    matchea la regex `DECLARED_GOVERNANCE_JSON_RE` (forma estructural de
    una config MCP, no la palabra suelta en prosa). Devuelve rutas
    relativas a la raíz.
    """
    candidates: list[str] = []
    suffixes = (".json", ".jsonc")
    skip_dirs = {".git", ".spec", ".gitnexus", ".venv", ".pytest_cache",
                 ".ruff_cache", ".tmp", ".codebase-memory"}
    for p in sorted(filesystem_root.rglob("*")):
        rel = p.relative_to(filesystem_root)
        if any(part in skip_dirs for part in rel.parts):
            continue
        if p.is_dir():
            continue
        if not p.name.endswith(suffixes):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if DECLARED_GOVERNANCE_JSON_RE.search(text):
            candidates.append(str(rel))
    return candidates


def compute_fs_hash(filesystem_root: Path) -> str:
    """Hash determinista de los archivos del filesystem (excluyendo dirs
    de sistema y binarios). Usa `find` + `sha256sum` para portabilidad con
    el patrón del repo (ver `.spec/scripts/validate_artifact_size.py:293`).
    """
    try:
        rel = subprocess.run(
            ["find", ".", "-type", "f",
             "-not", "-path", "./.git/*",
             "-not", "-path", "./.spec/*",
             "-not", "-path", "./.gitnexus/*",
             "-not", "-path", "./.venv/*",
             "-not", "-path", "./.pytest_cache/*",
             "-not", "-path", "./.ruff_cache/*",
             "-not", "-path", "./.tmp/*",
             "-not", "-path", "./.codebase-memory/*",
             "-print0"],
            cwd=filesystem_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return f"find-failed:{type(e).__name__}"

    paths = sorted(p for p in rel.stdout.split("\0") if p)
    if not paths:
        return hashlib.sha256(b"").hexdigest()

    h = hashlib.sha256()
    for p in paths:
        full = filesystem_root / p.lstrip("./")
        try:
            h.update(p.encode("utf-8"))
            h.update(b"\0")
            h.update(full.read_bytes())
            h.update(b"\0")
        except OSError:
            h.update(b"<unreadable>")
            h.update(b"\0")
    return h.hexdigest()


def check(unit_path: Path, spec_path: Path, filesystem_root: Path) -> Report:
    """Orquesta las tres verificaciones y devuelve un Report."""
    rpt = Report(
        verdict="ok",
        filesystem_root=str(filesystem_root.resolve()),
    )

    if not spec_path.is_file():
        rpt.verdict = "error"
        rpt.causes.append(f"spec no encontrado: {spec_path}")
        return rpt
    if not filesystem_root.is_dir():
        rpt.verdict = "error"
        rpt.causes.append(f"filesystem_root no es directorio: {filesystem_root}")
        return rpt

    declared = read_configuration_contract(spec_path)
    rpt.declared = declared

    found = find_declared_governance(filesystem_root)
    rpt.found = found

    rpt.fs_hash = compute_fs_hash(filesystem_root)

    if not declared:
        rpt.notes.append("spec no declara `configuration_contract:`; "
                         "el chequeo se reduce a la lista encontrada. "
                         "El skill decide el fallback.")
        declared_set = set()
    else:
        declared_set = set(declared)
        missing = [d for d in declared if not (filesystem_root / d).exists()]
        if missing:
            rpt.verdict = "configuration_contract-inconsistente"
            for m in missing:
                rpt.causes.append(
                    f"configuration_contract declara '{m}' pero el archivo no existe"
                )

    declared_with_marker = set(declared) if declared else set()
    undeclared_governance = [f for f in found if f not in declared_with_marker]
    if undeclared_governance and rpt.verdict == "ok":
        rpt.verdict = "superficie-no-declarada"
        for u in undeclared_governance:
            rpt.causes.append(
                f"'{u}' declara MCP/gob pero no está en configuration_contract"
            )

    return rpt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Auditor de superficie de gobernanza del kit (unit 0003, fix 7).",
    )
    parser.add_argument("--unit", type=Path, required=True,
                        help="Ruta al directorio de la unit (para contexto).")
    parser.add_argument("--spec", type=Path, required=True,
                        help="Ruta al spec.md que declara el configuration_contract.")
    parser.add_argument("--filesystem-root", type=Path, required=True,
                        help="Raíz del filesystem a auditar (típicamente el repo del kit o el worktree del destino).")
    args = parser.parse_args(argv)

    rpt = check(args.unit, args.spec, args.filesystem_root)
    print(json.dumps(rpt.to_dict(), indent=2, sort_keys=True, ensure_ascii=False))
    if rpt.verdict != "ok":
        for cause in rpt.causes:
            print(f"ERROR: {cause}", file=sys.stderr)
    return rpt.exit_code()


if __name__ == "__main__":
    sys.exit(main())
