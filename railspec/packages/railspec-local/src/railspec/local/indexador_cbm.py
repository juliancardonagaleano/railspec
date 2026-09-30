"""Indexador local sobre ``codebase-memory-mcp`` (DeusData, MIT) en modo CLI.

Se usa el modo ``cli`` del binario: corre una operación y termina, sin
demonio ni *watchers*, y nunca escribe el artefacto ``.codebase-memory/`` en
el árbol (``--persistence`` queda en falso). Para el delta:

- el árbol de trabajo de la unidad se indexa con un nombre de proyecto fijo;
- los archivos tocados, tal como estaban en el commit base, se extraen a un
  directorio temporal y se indexan aparte para saber qué símbolos y aristas
  desaparecen;
- los símbolos se identifican con ``id_simbolo`` del contrato (slug del
  repositorio, ruta, tipo y nombre calificado sin el prefijo del proyecto).

Pendiente: el binario no expone sus vectores por CLI, así que el delta sale
sin embeddings (el contrato los admite vacíos) y ``embedding_consulta``
devuelve None hasta que haya una vía para leerlos.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from railspec.contracts.snapshot import (
    Arista,
    DeltaIndice,
    MotorIndice,
    Relacion,
    Simbolo,
    TipoSimbolo,
    id_simbolo,
)

from . import git
from .rutas import coincide

BINARIO = "codebase-memory-mcp"

_TIPOS = {
    "Function": TipoSimbolo.funcion,
    "Method": TipoSimbolo.metodo,
    "Class": TipoSimbolo.clase,
    "Interface": TipoSimbolo.interfaz,
    "Module": TipoSimbolo.modulo,
    "Variable": TipoSimbolo.variable,
}
_RELACIONES = {
    "CALLS": Relacion.llama,
    "IMPORTS": Relacion.importa,
    "INHERITS": Relacion.hereda,
    "IMPLEMENTS": Relacion.implementa,
    "DEFINES": Relacion.define,
    "TESTS": Relacion.prueba,
}


class ErrorIndexador(RuntimeError):
    pass


@dataclass(frozen=True)
class _Nodo:
    id: str
    nombre: str
    tipo: TipoSimbolo
    ruta: str
    inicio: int
    fin: int


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", texto.lower()).strip("-")[:60] or "x"


class IndexadorCodebaseMemory:
    def __init__(self, binario: str) -> None:
        self.binario = binario
        self._version: str | None = None

    # --- CLI -------------------------------------------------------------------------

    def _cli(self, tool: str, argumentos: dict) -> dict:
        proc = subprocess.run(
            [self.binario, "cli", "--quiet", tool],
            input=json.dumps(argumentos).encode(),
            capture_output=True,
            timeout=600,
            check=False,
        )
        if proc.returncode != 0:
            raise ErrorIndexador(f"{tool}: {proc.stderr.decode('utf-8', 'replace').strip()[:500]}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ErrorIndexador(f"{tool}: salida no JSON") from exc

    def version(self) -> str:
        if self._version is None:
            salida = subprocess.run(
                [self.binario, "--version"], capture_output=True, timeout=60, check=True
            ).stdout.decode()
            encontrada = re.search(r"(\d+\.\d+\.\d+(?:[-+][\w.]+)?)", salida)
            if not encontrada:
                raise ErrorIndexador(f"versión ilegible: {salida!r}")
            self._version = encontrada.group(1)
        return self._version

    def _indexar(self, ruta: Path, proyecto: str) -> None:
        self._cli("index_repository", {"repo_path": str(ruta), "name": proyecto, "mode": "fast"})

    def _consultar(self, proyecto: str, cypher: str) -> list[list[str]]:
        filas: list[list[str]] = []
        cursor = None
        while True:
            args = {"project": proyecto, "query": cypher, "format": "json", "max_rows": 5000}
            if cursor:
                args["cursor"] = cursor
            salida = self._cli("query_graph", args)
            filas += salida.get("rows", [])
            cursor = salida.get("next_cursor") or salida.get("cursor")
            if not salida.get("has_more") or not cursor:
                return filas

    # --- lectura del grafo ------------------------------------------------------------

    def _grafo(
        self, proyecto: str, repositorio: str, rutas: list[str]
    ) -> tuple[dict[str, _Nodo], set[tuple[str, str, Relacion]]]:
        if not rutas:
            return {}, set()
        lista = json.dumps(rutas)
        prefijo = f"{proyecto}."
        nodos: dict[str, _Nodo] = {}
        filas = self._consultar(
            proyecto,
            "MATCH (n) WHERE n.file_path IN "
            + lista
            + " RETURN n.qualified_name, labels(n), n.file_path, n.start_line, n.end_line",
        )
        for calificado, etiquetas, ruta, inicio, fin in filas:
            nodo = self._nodo(prefijo, repositorio, calificado, etiquetas, ruta, inicio, fin)
            if nodo is not None:
                nodos[calificado] = nodo
        aristas: set[tuple[str, str, Relacion]] = set()
        filas = self._consultar(
            proyecto,
            "MATCH (a)-[r]->(b) WHERE a.file_path IN "
            + lista
            + " RETURN a.qualified_name, type(r), b.qualified_name, labels(b), b.file_path, "
            "b.start_line, b.end_line",
        )
        for origen, relacion, destino, etiquetas, ruta, inicio, fin in filas:
            rel = _RELACIONES.get(relacion)
            if rel is None or origen not in nodos:
                continue
            nodo_destino = nodos.get(destino) or self._nodo(
                prefijo, repositorio, destino, etiquetas, ruta, inicio, fin
            )
            if nodo_destino is not None:
                aristas.add((nodos[origen].id, nodo_destino.id, rel))
        return nodos, aristas

    @staticmethod
    def _nodo(
        prefijo: str, repositorio: str, calificado: str, etiquetas: str, ruta: str, inicio: str, fin: str
    ) -> _Nodo | None:
        tipo = next((_TIPOS[e] for e in json.loads(etiquetas or "[]") if e in _TIPOS), None)
        if tipo is None or not ruta or ruta.startswith("<") or ruta.startswith("/"):
            return None
        if not calificado.startswith(prefijo):
            return None  # externo al proyecto (builtins, dependencias)
        nombre = calificado.removeprefix(prefijo)
        inicio_i, fin_i = max(int(inicio or 1), 1), max(int(fin or 1), 1)
        return _Nodo(
            id=id_simbolo(repositorio, ruta, tipo.value, nombre),
            nombre=nombre,
            tipo=tipo,
            ruta=ruta,
            inicio=inicio_i,
            fin=max(fin_i, inicio_i),
        )

    # --- Indexador ---------------------------------------------------------------------

    def delta(
        self, worktree: Path, repositorio: str, base_commit: str, rutas: list[str], excluir: list[str]
    ) -> DeltaIndice:
        rutas = [r for r in rutas if not coincide(r, excluir)]
        motor = MotorIndice(version=self.version())
        if not rutas:
            return DeltaIndice(motor=motor)
        proyecto = f"railspec-{_slug(repositorio)}-{_slug(worktree.name)}"
        self._indexar(worktree, proyecto)
        despues = [r for r in rutas if (worktree / r).is_file()]
        nodos, aristas = self._grafo(proyecto, repositorio, despues)

        antes_rutas = _existentes_en(worktree, base_commit, rutas)
        nodos_antes: dict[str, _Nodo] = {}
        aristas_antes: set[tuple[str, str, Relacion]] = set()
        if antes_rutas:
            with tempfile.TemporaryDirectory(prefix="railspec-base-") as tmp:
                _extraer(worktree, base_commit, antes_rutas, Path(tmp))
                proyecto_base = f"{proyecto}-base"
                try:
                    self._indexar(Path(tmp), proyecto_base)
                    nodos_antes, aristas_antes = self._grafo(proyecto_base, repositorio, antes_rutas)
                finally:
                    self._borrar(proyecto_base)

        simbolos = [
            Simbolo(
                id=n.id,
                nombre=n.nombre,
                tipo=n.tipo,
                ruta=n.ruta,
                linea_inicio=n.inicio,
                linea_fin=n.fin,
                sha256=_sha_lineas(worktree / n.ruta, n.inicio, n.fin),
            )
            for n in sorted({n.id: n for n in nodos.values()}.values(), key=lambda n: (n.ruta, n.inicio))
        ]
        ids_despues = {s.id for s in simbolos}
        borrados = sorted({n.id for n in nodos_antes.values()} - ids_despues)
        return DeltaIndice(
            motor=motor,
            simbolos_upsert=simbolos,
            simbolos_borrados=borrados,
            aristas_agregadas=[
                Arista(origen=o, destino=d, relacion=r) for o, d, r in sorted(aristas - aristas_antes)
            ],
            aristas_borradas=[
                Arista(origen=o, destino=d, relacion=r) for o, d, r in sorted(aristas_antes - aristas)
            ],
        )

    def _borrar(self, proyecto: str) -> None:
        try:
            self._cli("delete_project", {"project": proyecto})
        except ErrorIndexador:
            pass

    def embedding_consulta(self, texto: str) -> bytes | None:
        return None


def _existentes_en(worktree: Path, commit: str, rutas: list[str]) -> list[str]:
    salida = git.git(worktree, "ls-tree", "-r", "--name-only", "-z", commit, "--", *rutas)
    return [r for r in salida.decode("utf-8").split("\0") if r]


def _extraer(worktree: Path, commit: str, rutas: list[str], destino: Path) -> None:
    crudo = git.git(worktree, "archive", "--format=tar", commit, "--", *rutas)
    with tarfile.open(fileobj=io.BytesIO(crudo)) as tar:
        tar.extractall(destino, filter="data")


def _sha_lineas(archivo: Path, inicio: int, fin: int) -> str:
    lineas = archivo.read_bytes().splitlines(keepends=True)
    return hashlib.sha256(b"".join(lineas[inicio - 1 : fin])).hexdigest()


def crear() -> IndexadorCodebaseMemory | None:
    """Fábrica del entry point; None si el binario no está instalado."""

    binario = shutil.which(BINARIO)
    return IndexadorCodebaseMemory(binario) if binario else None
