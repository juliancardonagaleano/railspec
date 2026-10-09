"""Indexador local sobre ``codebase-memory-mcp`` (DeusData, MIT).

El binario se lanza una vez como servidor MCP por stdio y todas las
operaciones de un índice (indexar, consultas paginadas, borrar) van por esa
sesión: arrancar el proceso cuesta varios segundos y las consultas, milésimas.
La sesión dura lo que un delta: no queda ningún proceso vivo entre reportes.
Se respeta la caché del usuario (``CBM_CACHE_DIR``): el binario mantiene un
demonio por cuenta y rechaza dos cachés distintas a la vez. Nunca se escribe
el artefacto ``.codebase-memory/`` en el árbol (``persistence`` queda en
falso). Las consultas piden páginas grandes: con el presupuesto de salida por
defecto el binario devuelve unas cien filas por página. Si la sesión no
arranca, se vuelve al modo ``cli`` (un proceso por operación).

La versión del binario está fijada (``VERSION_FIJA``): los ids de símbolo y las
aristas salen de su parser y de sus nombres calificados, y un delta calculado
con otra versión no casaría con el índice canónico que construye la CI. Al
arrancar (``crear``) se compara la versión instalada; si es otra, el indexador
queda apagado con un aviso que dice qué instalar y los snapshots viajan con
solo hashes. Subir la versión fijada es un PR que repasa las pruebas con el
binario nuevo. Para el delta:

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
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

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
#: Versión de ``codebase-memory-mcp`` con la que se verificó el indexador. Instalar:
#: ``pip install codebase-memory-mcp==$(python -m railspec.local.indexador_cbm)``.
VERSION_FIJA = "0.11.0"
#: Tope de filas por página y de salida que admite ``query_graph``.
MAX_FILAS = 99998
MAX_TOKENS_SALIDA = 1_000_000
TIMEOUT_S = 600

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

log = logging.getLogger(__name__)


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


#: ``C:\\…`` o ``C:/…``: una ruta absoluta de Windows (fuera del proyecto, como ``/…``).
_UNIDAD_WINDOWS = re.compile(r"[A-Za-z]:[\\/]")


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", texto.lower()).strip("-")[:60] or "x"


class _SesionMcp:
    """Cliente MCP mínimo y síncrono sobre stdio (JSON-RPC por líneas).

    Síncrono a propósito: el indexador se llama desde código síncrono del proxy
    y no debe depender de su bucle de eventos."""

    def __init__(self, binario: str) -> None:
        self._errores = tempfile.TemporaryFile()
        self._proc = subprocess.Popen(
            [binario],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._errores,
        )
        self._buffer = b""
        self._id = 0
        # Un hilo lee stdout y deja los trozos en una cola: ``selectors`` no sirve con tuberías en Windows.
        self._trozos: queue.Queue[bytes] = queue.Queue()
        self._fin = False
        self._lector = threading.Thread(target=self._leer_salida, name="railspec-indexador", daemon=True)
        self._lector.start()
        try:
            self._pedir(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "railspec-local", "version": "0"},
                },
                timeout_s=120,
            )
            self._enviar({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self.cerrar()
            raise

    @property
    def viva(self) -> bool:
        return self._proc.poll() is None

    def _enviar(self, mensaje: dict[str, Any]) -> None:
        stdin: IO[bytes] = self._proc.stdin  # type: ignore[assignment]
        try:
            stdin.write(json.dumps(mensaje).encode() + b"\n")
            stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ErrorIndexador(f"la sesión de {BINARIO} se cerró: {self._stderr()}") from exc

    def _leer_salida(self) -> None:
        stdout: IO[bytes] = self._proc.stdout  # type: ignore[assignment]
        try:
            while trozo := os.read(stdout.fileno(), 1 << 16):
                self._trozos.put(trozo)
        except (OSError, ValueError):  # el flujo se cerró bajo el hilo
            pass
        self._trozos.put(b"")

    def _linea(self, limite: float) -> bytes:
        while b"\n" not in self._buffer:
            if self._fin:
                raise ErrorIndexador(f"la sesión de {BINARIO} terminó: {self._stderr()}")
            restante = limite - time.monotonic()
            if restante <= 0:
                raise ErrorIndexador(f"{BINARIO} no respondió a tiempo")
            try:
                trozo = self._trozos.get(timeout=restante)
            except queue.Empty:
                continue
            if trozo:
                self._buffer += trozo
            else:
                self._fin = True
        linea, self._buffer = self._buffer.split(b"\n", 1)
        return linea

    def _pedir(self, metodo: str, parametros: dict[str, Any], timeout_s: float = TIMEOUT_S) -> dict:
        self._id += 1
        propio = self._id
        self._enviar({"jsonrpc": "2.0", "id": propio, "method": metodo, "params": parametros})
        limite = time.monotonic() + timeout_s
        while True:
            try:
                mensaje = json.loads(self._linea(limite))
            except json.JSONDecodeError:
                continue  # líneas que no son JSON-RPC
            if not isinstance(mensaje, dict) or "id" not in mensaje:
                continue  # notificaciones (progreso, registros)
            if "method" in mensaje:
                # Petición del servidor (ping, roots): se contesta sin capacidades.
                respuesta: dict[str, Any] = {"jsonrpc": "2.0", "id": mensaje["id"]}
                if mensaje["method"] == "ping":
                    respuesta["result"] = {}
                else:
                    respuesta["error"] = {"code": -32601, "message": "no soportado"}
                self._enviar(respuesta)
                continue
            if mensaje.get("id") != propio:
                continue
            if "error" in mensaje:
                raise ErrorIndexador(f"{metodo}: {json.dumps(mensaje['error'])[:500]}")
            return mensaje.get("result") or {}

    def llamar(self, tool: str, argumentos: dict[str, Any]) -> dict:
        resultado = self._pedir("tools/call", {"name": tool, "arguments": argumentos})
        datos = resultado.get("structuredContent")
        if datos is None:
            texto = "".join(b.get("text", "") for b in resultado.get("content", []) if isinstance(b, dict))
            try:
                datos = json.loads(texto)
            except json.JSONDecodeError as exc:
                if resultado.get("isError"):
                    raise ErrorIndexador(f"{tool}: {texto[:500]}") from exc
                raise ErrorIndexador(f"{tool}: salida no JSON") from exc
        if resultado.get("isError"):
            raise ErrorIndexador(f"{tool}: {json.dumps(datos, ensure_ascii=False)[:500]}")
        return datos

    def _stderr(self) -> str:
        try:
            self._errores.seek(0)
            return self._errores.read()[-500:].decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            return ""

    def cerrar(self) -> None:
        if self._proc.poll() is None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self._proc.kill()
                self._proc.wait(timeout=10)
        self._lector.join(timeout=5)
        for flujo in (self._proc.stdout, self._errores):
            if flujo is not None:
                flujo.close()


class IndexadorCodebaseMemory:
    def __init__(self, binario: str, version: str | None = None) -> None:
        self.binario = binario
        self._version = version  # ya leída por ``diagnosticar``; si no, se pregunta al binario
        self._sesion: _SesionMcp | None = None
        self._solo_cli = False

    # --- transporte -----------------------------------------------------------------

    def _llamar(self, tool: str, argumentos: dict) -> dict:
        if not self._solo_cli:
            if self._sesion is None or not self._sesion.viva:
                try:
                    self._sesion = _SesionMcp(self.binario)
                except (ErrorIndexador, OSError):
                    # Sin modo servidor (o sin arrancar): un proceso por operación.
                    self._sesion, self._solo_cli = None, True
            if self._sesion is not None:
                return self._sesion.llamar(tool, argumentos)
        return self._cli(tool, argumentos)

    def _cli(self, tool: str, argumentos: dict) -> dict:
        proc = subprocess.run(
            [self.binario, "cli", "--quiet", tool],
            input=json.dumps(argumentos).encode(),
            capture_output=True,
            timeout=TIMEOUT_S,
            check=False,
        )
        if proc.returncode != 0:
            raise ErrorIndexador(f"{tool}: {proc.stderr.decode('utf-8', 'replace').strip()[:500]}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ErrorIndexador(f"{tool}: salida no JSON") from exc

    def cerrar(self) -> None:
        if self._sesion is not None:
            sesion, self._sesion = self._sesion, None
            sesion.cerrar()

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
        self._llamar("index_repository", {"repo_path": str(ruta), "name": proyecto, "mode": "fast"})

    def _consultar(self, proyecto: str, cypher: str) -> list[list[str]]:
        filas: list[list[str]] = []
        cursor = None
        while True:
            args: dict[str, Any] = {
                "project": proyecto,
                "query": cypher,
                "format": "json",
                "max_rows": MAX_FILAS,
                "max_output_tokens": MAX_TOKENS_SALIDA,
            }
            if cursor:
                args["cursor"] = cursor
            salida = self._llamar("query_graph", args)
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
        if tipo is None or not ruta or ruta.startswith(("<", "/")) or _UNIDAD_WINDOWS.match(ruta):
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
        try:
            return self._delta(worktree, repositorio, base_commit, rutas, motor)
        finally:
            self.cerrar()

    def _delta(
        self, worktree: Path, repositorio: str, base_commit: str, rutas: list[str], motor: MotorIndice
    ) -> DeltaIndice:
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
            self._llamar("delete_project", {"project": proyecto})
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


@dataclass(frozen=True)
class DiagnosticoBinario:
    """Qué binario de ``codebase-memory-mcp`` hay en la máquina frente a la versión fijada."""

    binario: str | None
    #: ``None`` si no hay binario o su versión no se pudo leer.
    version: str | None
    fijada: str = VERSION_FIJA

    @property
    def compatible(self) -> bool:
        return self.binario is not None and self.version == self.fijada

    @property
    def problema(self) -> str | None:
        """Texto para el desarrollador; ``None`` si el binario sirve."""

        if self.binario is None:
            return f"{BINARIO} no está instalado."
        instalar = f"pip install {BINARIO}=={self.fijada}"
        if self.version is None:
            detalle = f"no se pudo leer la versión de {self.binario}"
        elif self.version != self.fijada:
            detalle = f"{BINARIO} {self.version} no es la versión fijada {self.fijada}"
        else:
            return None
        return (
            f"{detalle}. El indexado local queda apagado y los snapshots viajan solo con hashes; "
            f"instala la fijada: {instalar}"
        )


def diagnosticar(binario: str | None = None) -> DiagnosticoBinario:
    """Busca el binario (en el PATH si no se da) y lee su versión, sin fallar si no responde."""

    binario = binario or shutil.which(BINARIO)
    if binario is None:
        return DiagnosticoBinario(binario=None, version=None)
    try:
        version = IndexadorCodebaseMemory(binario).version()
    except (ErrorIndexador, subprocess.SubprocessError, OSError):
        version = None
    return DiagnosticoBinario(binario=binario, version=version)


def crear() -> IndexadorCodebaseMemory | None:
    """Fábrica del entry point; None si el binario no está instalado o no es la versión fijada."""

    diagnostico = diagnosticar()
    if diagnostico.binario is None:
        return None
    if not diagnostico.compatible:
        log.warning("%s", diagnostico.problema)
        return None
    return IndexadorCodebaseMemory(diagnostico.binario, version=diagnostico.version)


def main(argv: list[str] | None = None) -> int:
    """``python -m railspec.local.indexador_cbm``: imprime la versión fijada; ``--verificar`` además
    comprueba que la instalada es esa y sale con 1 si no (lo usa la CI)."""

    argumentos = sys.argv[1:] if argv is None else argv
    if argumentos == ["--verificar"]:
        diagnostico = diagnosticar()
        if not diagnostico.compatible:
            print(diagnostico.problema, file=sys.stderr)
            return 1
    elif argumentos:
        print("uso: python -m railspec.local.indexador_cbm [--verificar]", file=sys.stderr)
        return 2
    print(VERSION_FIJA)
    return 0


if __name__ == "__main__":
    sys.exit(main())
