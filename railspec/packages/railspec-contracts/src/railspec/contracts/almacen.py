"""Interfaces de almacenamiento e identidad.

Los hilos de motor, proxy y repositorio central implementan contra estas
interfaces: Mongo primero (``StateStore``), FalkorDB para grafo y vectores
(``GraphStore``, ``VectorStore``) con LadybugDB como alternativa, y GitHub
como proveedor de identidad. Cada método exige su alcance tipado: no hay
forma de consultar sin workspace.

``CheckpointStorage`` es el protocolo de Microsoft Agent Framework; su
implementación sobre Mongo pertenece a la fase 2 y no se redefine aquí.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .comun import Actor, AlcanceRepositorio, AlcanceUnidad, AlcanceWorkspace, Commit
from .estado import EstadoUnidad
from .eventos import Direccion, EventoSync
from .repositorio import RegistroAuditoria, TelemetriaNodo
from .snapshot import DeltaIndice, Embedding
from .tools import GraphQueryEntrada, GraphQuerySalida, TelemetryQueryEntrada, TelemetryQuerySalida


class ConflictoVersion(Exception):
    """La versión esperada no coincide con la almacenada (bloqueo optimista)."""

    def __init__(self, esperada: int, actual: int) -> None:
        super().__init__(f"versión esperada {esperada}, almacenada {actual}")
        self.esperada = esperada
        self.actual = actual


@runtime_checkable
class StateStore(Protocol):
    def obtener_estado(self, alcance: AlcanceUnidad) -> EstadoUnidad | None: ...

    def guardar_estado(self, estado: EstadoUnidad, version_esperada: int | None) -> EstadoUnidad:
        """Escribe con ``version = version_esperada + 1``; None solo al crear.

        Lanza ``ConflictoVersion`` si otra escritura ganó.
        """

    def registrar_evento(self, evento: EventoSync) -> bool:
        """Idempotente por ``evento.id``: devuelve False si ya existía."""

    def eventos_desde(
        self, alcance: AlcanceUnidad, direccion: Direccion, secuencia: int
    ) -> list[EventoSync]: ...

    def registrar_auditoria(self, registro: RegistroAuditoria) -> None: ...

    def registrar_telemetria(self, fila: TelemetriaNodo) -> None: ...

    def consultar_telemetria(self, consulta: TelemetryQueryEntrada) -> TelemetryQuerySalida: ...


@runtime_checkable
class VectorStore(Protocol):
    def upsert(self, alcance: AlcanceRepositorio, commit: Commit, embeddings: list[Embedding]) -> None: ...

    def buscar(self, alcance: AlcanceRepositorio, vector_b64: str, k: int) -> list[tuple[str, float]]:
        """Devuelve (simbolo_id, puntuación); nunca cruza repositorios."""

    def borrar_repositorio(self, alcance: AlcanceRepositorio) -> None: ...


@runtime_checkable
class GraphStore(Protocol):
    def aplicar_delta(
        self, alcance: AlcanceRepositorio, commit: Commit, delta: DeltaIndice, unidad: str | None
    ) -> None:
        """Con ``unidad`` aplica a la superposición de esa unidad; sin ella, al canónico."""

    def consultar(self, consulta: GraphQueryEntrada, visibles: list[AlcanceRepositorio]) -> GraphQuerySalida:
        """``visibles`` lo calcula el servidor desde los vínculos y roles del actor."""

    def descartar_superposicion(self, alcance: AlcanceRepositorio, unidad: str, hasta: Commit) -> None: ...

    def borrar_repositorio(self, alcance: AlcanceRepositorio) -> None: ...


@runtime_checkable
class ProveedorIdentidad(Protocol):
    """GitHub hoy; otro proveedor (Azure DevOps, Entra ID) entra por aquí."""

    nombre: str

    def actor_desde_token(self, token: str, canal: str) -> Actor: ...

    def workspaces_visibles(self, actor: Actor, org: str) -> list[AlcanceWorkspace]: ...

    def expira(self, token: str) -> datetime | None: ...


__all__ = [
    "ConflictoVersion",
    "GraphStore",
    "ProveedorIdentidad",
    "StateStore",
    "VectorStore",
]
