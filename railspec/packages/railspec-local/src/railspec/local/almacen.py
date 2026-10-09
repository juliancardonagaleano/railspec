"""Persistencia del estado local en el worktree de la unidad.

``.railspec/estado-local.json`` guarda el ``EstadoLocal`` del contrato: espejo
de solo lectura del estado remoto, orden en curso y cola de eventos sin
confirmar. Los reportes que esperan conexión se guardan completos en
``.railspec/pendientes/<orden>.json``, porque el evento de la cola solo lleva
el id de la orden; ``<orden>.enviado`` marca que ya salió al menos una vez.
``.railspec/ultimo-empuje`` guarda el último commit de la rama de la unidad
avisado como ``commit.empujado``.

Cada escritura es atómica (archivo temporal + ``rename``) y las secciones de
lectura-modificación-escritura se serializan con un candado de archivo para que dos
procesos del proxy sobre la misma unidad no se pisen (``fcntl`` en POSIX, ``msvcrt`` en Windows: ver
``plataforma.py``).
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from railspec.contracts.estado import BloqueoInstancia, EstadoLocal
from railspec.contracts.reporte import ReporteOrden

from .errores import UnidadEnUso
from .plataforma import pid_vivo, soltar_candado, tomar_candado

DIR = Path(".railspec")
ARCHIVO_ESTADO = DIR / "estado-local.json"
DIR_PENDIENTES = DIR / "pendientes"
ARCHIVO_CERROJO = DIR / ".cerrojo"
ARCHIVO_EMPUJE = DIR / "ultimo-empuje"

#: Rutas locales del proxy que nunca se versionan (van a ``info/exclude``).
EXCLUIR_DE_GIT = [
    "/.railspec/estado-local.json",
    "/.railspec/pendientes/",
    "/.railspec/validacion/",
    "/.railspec/insumos/",
    "/.railspec/.cerrojo",
    "/.railspec/ultimo-empuje",
    "/.railspec/busqueda.sqlite*",
]


def _escribir_atomico(ruta: Path, contenido: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(f".{ruta.name}.{os.getpid()}.tmp")
    tmp.write_text(contenido, encoding="utf-8")
    os.replace(tmp, ruta)


class Almacen:
    def __init__(self, worktree: Path) -> None:
        self.worktree = worktree

    @property
    def ruta(self) -> Path:
        return self.worktree / ARCHIVO_ESTADO

    def existe(self) -> bool:
        return self.ruta.is_file()

    def leer(self) -> EstadoLocal:
        return EstadoLocal.model_validate_json(self.ruta.read_text(encoding="utf-8"))

    def escribir(self, estado: EstadoLocal) -> None:
        _escribir_atomico(self.ruta, estado.model_dump_json(indent=2) + "\n")

    @contextmanager
    def cerrojo(self) -> Iterator[None]:
        ruta = self.worktree / ARCHIVO_CERROJO
        ruta.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(ruta, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            tomar_candado(fd)
            try:
                yield
            finally:
                soltar_candado(fd)
        finally:
            os.close(fd)

    # --- una sesión por unidad y máquina ---------------------------------------

    def tomar_bloqueo(self, estado: EstadoLocal, pid: int | None = None) -> EstadoLocal:
        pid = pid or os.getpid()
        host = socket.gethostname()
        actual = estado.bloqueo
        if actual is not None and actual.pid != pid and actual.host == host and pid_vivo(actual.pid):
            raise UnidadEnUso(
                f"La unidad {estado.unidad.unidad} ya la atiende otra sesión del proxy (pid {actual.pid}). "
                "Ciérrala o usa esa sesión."
            )
        if actual is not None and actual.pid == pid and actual.host == host:
            return estado
        return estado.model_copy(
            update={"bloqueo": BloqueoInstancia(host=host, pid=pid, desde=datetime.now(UTC))}
        )

    # --- reportes pendientes ------------------------------------------------------

    def guardar_pendiente(self, reporte: ReporteOrden) -> None:
        ruta = self.worktree / DIR_PENDIENTES / f"{reporte.orden_id}.json"
        _escribir_atomico(ruta, reporte.model_dump_json(indent=2) + "\n")

    def leer_pendiente(self, orden_id: UUID) -> ReporteOrden | None:
        ruta = self.worktree / DIR_PENDIENTES / f"{orden_id}.json"
        if not ruta.is_file():
            return None
        return ReporteOrden.model_validate_json(ruta.read_text(encoding="utf-8"))

    def marcar_enviado(self, orden_id: UUID) -> None:
        (self.worktree / DIR_PENDIENTES / f"{orden_id}.enviado").touch()

    def fue_enviado(self, orden_id: UUID) -> bool:
        return (self.worktree / DIR_PENDIENTES / f"{orden_id}.enviado").is_file()

    def borrar_pendiente(self, orden_id: UUID) -> None:
        (self.worktree / DIR_PENDIENTES / f"{orden_id}.json").unlink(missing_ok=True)
        (self.worktree / DIR_PENDIENTES / f"{orden_id}.enviado").unlink(missing_ok=True)

    # --- último commit empujado avisado ----------------------------------------------

    def leer_ultimo_empuje(self) -> str | None:
        ruta = self.worktree / ARCHIVO_EMPUJE
        return ruta.read_text(encoding="utf-8").strip() or None if ruta.is_file() else None

    def escribir_ultimo_empuje(self, commit: str) -> None:
        _escribir_atomico(self.worktree / ARCHIVO_EMPUJE, commit + "\n")
