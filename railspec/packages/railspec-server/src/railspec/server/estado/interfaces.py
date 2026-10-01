"""Interfaces de almacenamiento propias del motor.

``StateStore`` (contratos) cubre estado, eventos, auditoría y telemetría. El
motor necesita además órdenes emitidas, reportes, snapshots, entradas
pendientes de entregar al DAG, un turno exclusivo por unidad y lectura de la
configuración editable desde la consola. Todo método exige su alcance tipado,
igual que en los contratos: no hay consulta sin workspace.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import TypeAdapter
from railspec.contracts.comun import AlcanceRepositorio, AlcanceUnidad, AlcanceWorkspace, Perfil, Proveedor
from railspec.contracts.estado import EstadoUnidad
from railspec.contracts.orden import OrdenDeTrabajo
from railspec.contracts.reporte import ReporteOrden
from railspec.contracts.repositorio import (
    AsignacionRol,
    ModeloCatalogo,
    PerfilConfig,
    PresupuestoConfig,
    ProveedorContexto,
    VinculoRepositorio,
    Workspace,
)
from railspec.contracts.snapshot import Snapshot
from railspec.contracts.tools import UnitListEntrada

ADAPTADOR_ORDEN: TypeAdapter[OrdenDeTrabajo] = TypeAdapter(OrdenDeTrabajo)


class TipoEntrada(StrEnum):
    """Qué espera el DAG: el reporte de una orden o la resolución de un checkpoint."""

    reporte = "reporte"
    resolucion = "resolucion"


@dataclass(frozen=True)
class EntradaPendiente:
    """Respuesta recibida por una tool y aún no entregada al DAG.

    Sobrevive a la caída del proceso: si el servidor muere entre aceptar un
    reporte y reanudar el workflow, el siguiente turno la entrega.
    """

    alcance: AlcanceUnidad
    request_id: str
    tipo: TipoEntrada
    carga: dict[str, Any]
    recibida_en: datetime


@runtime_checkable
class AlmacenMotor(Protocol):
    # --- unidades -------------------------------------------------------------
    def siguiente_numero_unidad(self, alcance: AlcanceWorkspace) -> int:
        """Contador atómico por workspace para el prefijo NNNN del id de unidad."""

    def listar_estados(self, consulta: UnitListEntrada) -> tuple[list[EstadoUnidad], str | None]: ...

    def reclamar_importacion(
        self, alcance: AlcanceWorkspace, repositorio: str, tipo: str, id_original: str, unidad: str
    ) -> str:
        """Asocia un origen importado (``unit.import``) a ``unidad`` si nadie lo hizo antes.

        Devuelve la unidad asociada al origen: ``unidad`` la primera vez, la existente después.
        """

    # --- órdenes, reportes y snapshots --------------------------------------------
    def guardar_orden(self, orden: OrdenDeTrabajo) -> None: ...

    def obtener_orden(self, alcance: AlcanceUnidad, orden_id: str) -> OrdenDeTrabajo | None: ...

    def guardar_reporte(self, reporte: ReporteOrden) -> None:
        """Guarda el reporte sin su snapshot (va aparte, con TTL)."""

    def guardar_snapshot(self, snapshot: Snapshot) -> None: ...

    def obtener_snapshot(self, alcance: AlcanceUnidad, snapshot_id: str) -> Snapshot | None: ...

    # --- entradas pendientes y turno ------------------------------------------------
    def registrar_entrada(self, entrada: EntradaPendiente) -> bool:
        """Idempotente por ``request_id``: False si ya estaba registrada."""

    def entradas_pendientes(self, alcance: AlcanceUnidad) -> list[EntradaPendiente]: ...

    def consumir_entrada(self, alcance: AlcanceUnidad, request_id: str) -> None: ...

    def tomar_turno(self, alcance: AlcanceUnidad, dueno: str, ahora: datetime, ttl_s: int) -> bool:
        """Turno exclusivo para avanzar el DAG de una unidad entre réplicas."""

    def soltar_turno(self, alcance: AlcanceUnidad, dueno: str) -> None: ...

    # --- configuración (la edita la consola) ------------------------------------------
    def perfil(self, alcance: AlcanceWorkspace, nombre: Perfil) -> PerfilConfig | None:
        """El del workspace si existe; si no, el de la organización."""

    def presupuesto(self, alcance: AlcanceWorkspace) -> PresupuestoConfig | None: ...

    def vinculo(self, alcance: AlcanceRepositorio) -> VinculoRepositorio | None: ...

    def asignaciones(
        self, org: str, github_id: int, equipos: frozenset[int] = frozenset()
    ) -> list[AsignacionRol]:
        """De la persona y de los equipos de GitHub (``equipo_id``) a los que se sabe que pertenece."""

    def workspace(self, alcance: AlcanceWorkspace) -> Workspace | None: ...

    def proveedores_contexto(self, alcance: AlcanceWorkspace) -> list[ProveedorContexto]:
        """Los de la organización con los del workspace encima."""

    # --- caché de nodos de modelo (por organización, con caducidad) --------------------------
    def nodo_en_cache(self, org: str, clave: str, ahora: datetime) -> dict[str, Any] | None: ...

    def guardar_nodo_en_cache(
        self, org: str, clave: str, respuesta: dict[str, Any], expira: datetime
    ) -> None: ...

    # --- catálogo de modelos (por organización, leído por API) -----------------------------
    def catalogo(self, org: str) -> list[ModeloCatalogo]: ...

    def guardar_catalogo(self, org: str, proveedor: Proveedor, modelos: Iterable[ModeloCatalogo]) -> None: ...
