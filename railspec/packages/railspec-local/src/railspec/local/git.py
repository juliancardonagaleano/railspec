"""Operaciones git del proxy: worktree por unidad, hash del árbol y cambios.

Todo pasa por ``git`` como subproceso, con ``-C`` explícito: el proxy nunca
cambia el directorio de trabajo del proceso.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errores import ErrorRailspec

PREFIJO_RAMA = "railspec/"


class ErrorGit(ErrorRailspec):
    pass


def git(repo: Path, *args: str, entorno: dict[str, str] | None = None, entrada: bytes | None = None) -> bytes:
    env = None if entorno is None else {**os.environ, **entorno}
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=entrada,
        capture_output=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        detalle = proc.stderr.decode("utf-8", "replace").strip()
        raise ErrorGit(f"git {' '.join(args)} falló: {detalle}")
    return proc.stdout


def texto(repo: Path, *args: str) -> str:
    return git(repo, *args).decode("utf-8").strip()


def raiz_repositorio(desde: Path) -> Path:
    return Path(texto(desde, "rev-parse", "--show-toplevel"))


def head(repo: Path) -> str:
    return texto(repo, "rev-parse", "HEAD")


def rama_actual(repo: Path) -> str | None:
    try:
        return texto(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    except ErrorGit:
        return None


def hay_cambios(repo: Path) -> bool:
    return bool(texto(repo, "status", "--porcelain"))


def rama_de_unidad(unidad: str) -> str:
    return f"{PREFIJO_RAMA}{unidad}"


def excluir_localmente(repo: Path, patrones: list[str]) -> None:
    """Añade patrones a ``info/exclude`` (común a todos los worktrees), sin duplicar."""

    ruta = Path(texto(repo, "rev-parse", "--git-common-dir"))
    if not ruta.is_absolute():
        ruta = repo / ruta
    exclude = ruta / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    actuales = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
    nuevos = [p for p in patrones if p not in actuales]
    if nuevos:
        with exclude.open("a", encoding="utf-8") as f:
            if actuales and actuales[-1] != "":
                f.write("\n")
            f.write("\n".join(nuevos) + "\n")


def crear_worktree(repo: Path, destino: Path, rama: str, base_commit: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-b", rama, str(destino), base_commit)


def worktrees(repo: Path) -> dict[str, Path]:
    """Worktrees de unidades Railspec: rama ``railspec/<unidad>`` → ruta."""

    salida = texto(repo, "worktree", "list", "--porcelain")
    encontrados: dict[str, Path] = {}
    ruta: Path | None = None
    for linea in salida.splitlines():
        if linea.startswith("worktree "):
            ruta = Path(linea.removeprefix("worktree "))
        elif linea.startswith("branch ") and ruta is not None:
            rama = linea.removeprefix("branch refs/heads/")
            if rama.startswith(PREFIJO_RAMA):
                encontrados[rama.removeprefix(PREFIJO_RAMA)] = ruta
    return encontrados


def commit_empujado(repo: Path, rama: str) -> str | None:
    """Commit de ``rama`` según la referencia remota más reciente (``refs/remotes/*/<rama>``).

    Git la actualiza en cada ``push`` y ``fetch``; ``None`` si la rama nunca se empujó."""

    salida = texto(
        repo,
        "for-each-ref",
        "--sort=-committerdate",
        "--format=%(objectname)",
        f"refs/remotes/*/{rama}",
    )
    return salida.splitlines()[0] if salida else None


def hash_arbol(repo: Path) -> str:
    """``git add -A && git write-tree`` sobre un índice temporal: incluye lo no commiteado
    sin tocar el índice real del desarrollador."""

    indice_real = Path(texto(repo, "rev-parse", "--git-path", "index"))
    if not indice_real.is_absolute():
        indice_real = repo / indice_real
    with tempfile.TemporaryDirectory(prefix="railspec-idx-") as tmp:
        indice = Path(tmp) / "index"
        if indice_real.exists():
            # copy2 conserva el mtime del índice: git lo usa para detectar archivos
            # "racily clean" (mismo tamaño y segundo); con un mtime nuevo los daría por limpios.
            shutil.copy2(indice_real, indice)
        env = {"GIT_INDEX_FILE": str(indice)}
        git(repo, "add", "-A", entorno=env)
        return git(repo, "write-tree", entorno=env).decode().strip()


@dataclass(frozen=True)
class Cambio:
    estado: str  # A, M, D, R
    ruta: str
    ruta_anterior: str | None = None


def cambios(repo: Path, base: str, arbol: str) -> list[Cambio]:
    # Sin renombres primero; solo los renombres exactos (mismo contenido) se funden
    # después, para no depender de la heurística de similitud de git.
    salida = git(repo, "diff-tree", "-r", "-z", "--no-renames", "--name-status", base, arbol)
    campos = [c for c in salida.decode("utf-8").split("\0") if c]
    simples = [Cambio(campos[i][0], campos[i + 1]) for i in range(0, len(campos), 2)]
    renombres = _renombres(repo, base, arbol)
    if not renombres:
        return simples
    origenes = {r.ruta_anterior for r in renombres}
    destinos = {r.ruta for r in renombres}
    resto = [c for c in simples if c.ruta not in origenes and c.ruta not in destinos]
    return sorted(resto + renombres, key=lambda c: c.ruta)


def _renombres(repo: Path, base: str, arbol: str) -> list[Cambio]:
    salida = git(repo, "diff-tree", "-r", "-z", "-M100%", "--name-status", "--diff-filter=R", base, arbol)
    campos = [c for c in salida.decode("utf-8").split("\0") if c]
    return [Cambio("R", campos[i + 2], campos[i + 1]) for i in range(0, len(campos), 3)]


def contenido(repo: Path, objeto: str, ruta: str) -> bytes:
    return git(repo, "cat-file", "blob", f"{objeto}:{ruta}")


def sha256_blob(repo: Path, objeto: str, ruta: str) -> str:
    return hashlib.sha256(contenido(repo, objeto, ruta)).hexdigest()


def diff(repo: Path, base: str, arbol: str, excluir: list[str]) -> bytes:
    especificaciones = ["--", "."] + [f":(exclude){r}" for r in excluir]
    return git(repo, "diff", "--no-color", "-M100%", base, arbol, *especificaciones)
