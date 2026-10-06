"""Primitivas que un motor de grafo físico ofrece a Railspec.

Toda la lógica (superposiciones, analítica, riesgo, RAG, aislamiento) vive
en Python por encima de este protocolo, así que cambiar FalkorDB (SSPL) por
LadybugDB (MIT) u otro motor es implementar ``MotorGrafo`` y nada más.

Cada método recibe el nombre del grafo físico, pero solo ``acceso`` lo
construye: el resto del paquete trabaja con un ``Espacio`` ya atado a su
workspace y repositorio.

Lo que un motor guarda por símbolo es estructura y hashes: id, nombre,
tipo, ruta, rango de líneas, sha256 del fragmento y, opcionalmente, su
embedding. Nunca texto de código, en ningún nivel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

#: Propiedades de un símbolo persistidas por el motor. Una prueba verifica
#: que ningún motor guarde otra cosa (en particular, texto de código).
PROPIEDADES_SIMBOLO = ("id", "nombre", "tipo", "ruta", "linea_inicio", "linea_fin", "sha256")


@dataclass(frozen=True)
class AristaMotor:
    origen: str
    destino: str
    relacion: str


@dataclass(frozen=True)
class Cluster:
    id: str
    nombre: str
    miembros: tuple[str, ...]


@dataclass(frozen=True)
class Proceso:
    id: str
    nombre: str
    entrada: str
    pasos: tuple[str, ...]


@dataclass(frozen=True)
class Traza:
    """Un símbolo que una unidad tocó al completar tareas de un criterio ``CA-NN``."""

    unidad: str
    criterio: str
    simbolo: str


@dataclass
class Meta:
    """Metadatos del grafo: commit, superposición, lápidas y lotes de indexado.

    ``borrados`` y ``aristas_borradas`` son las lápidas de una superposición o
    lo que un índice incremental borra; ``base``, ``lotes`` y ``recibidos``
    solo los usa el grafo de preparación de ``graph.index``. ``integrado`` lo
    pone ``unit.integrate`` en la superposición de una unidad ya integrada: el
    commit que el índice canónico debe alcanzar para que pueda retirarse.

    ``actualizado`` (UTC) es la última actividad de una superposición (cada
    snapshot la reconstruye) o de un grafo de preparación (cada lote nuevo): lo
    que ``limpiar_huerfanos`` compara para borrar lo abandonado. Sin valor, el
    primer barrido lo sella (``MotorGrafo.sellar``). Lo lleva el motor aparte
    del resto de la meta para que una réplica anterior siga leyéndola.

    En el grafo canónico ``actualizado`` es el instante en que se aplicó el último
    índice (``graph.index``): ``graph.query`` lo devuelve como frescura y nadie lo barre.
    """

    commit: str | None = None
    unidad: str | None = None
    borrados: list[str] = field(default_factory=list)
    aristas_borradas: list[tuple[str, str, str]] = field(default_factory=list)
    base: str | None = None
    lotes: int | None = None
    recibidos: list[int] = field(default_factory=list)
    integrado: str | None = None
    actualizado: datetime | None = None


@runtime_checkable
class MotorGrafo(Protocol):
    # --- ciclo de vida -----------------------------------------------------
    def existe(self, grafo: str) -> bool: ...

    def borrar(self, grafo: str) -> None: ...

    def listar(self, prefijo: str) -> list[str]: ...

    def leer_meta(self, grafo: str) -> Meta: ...

    def escribir_meta(self, grafo: str, meta: Meta) -> None: ...

    def sellar(self, grafo: str, instante: datetime) -> None:
        """Fija ``actualizado`` solo si el grafo no lo tiene.

        No toca el resto de la meta y no crea el grafo: es atómico frente a un
        ``escribir_meta`` concurrente, que ya trae su propio sello.
        """

    # --- símbolos y aristas ------------------------------------------------
    def upsert_simbolos(self, grafo: str, simbolos: list[dict]) -> None:
        """Cada dict trae exactamente ``PROPIEDADES_SIMBOLO``; convierte stubs en símbolos."""

    def borrar_simbolos(self, grafo: str, ids: list[str]) -> None:
        """Borra el símbolo, sus aristas y su embedding."""

    def agregar_aristas(self, grafo: str, aristas: list[AristaMotor]) -> None:
        """Crea nodos stub (solo id) para extremos que no están en el grafo.

        Un stub es la forma de una referencia a otro repositorio del
        workspace: el id es global, el símbolo completo vive en su grafo.
        """

    def borrar_aristas(self, grafo: str, aristas: list[AristaMotor]) -> None: ...

    def simbolos(self, grafo: str, ids: list[str]) -> dict[str, dict]:
        """Solo símbolos completos; los stubs no aparecen."""

    def buscar_nombre(
        self, grafo: str, texto: str, tipos: list[str], exacto: bool, limite: int
    ) -> list[dict]:
        """``exacto``: nombre igual o sufijo calificado (``.x``, ``::x``, ``/x``, ``#x``).

        Sin ``exacto``: subcadena, sin distinguir mayúsculas.
        """

    def aristas(self, grafo: str, ids: list[str], relaciones: list[str], direccion: str) -> list[AristaMotor]:
        """``direccion``: ``salida`` (origen en ids) o ``entrada`` (destino en ids).

        Relaciones vacías = todas.
        """

    def todos_simbolos(self, grafo: str) -> list[dict]: ...

    def todas_aristas(self, grafo: str) -> list[AristaMotor]: ...

    # --- vectores ----------------------------------------------------------
    def fijar_embeddings(self, grafo: str, vectores: dict[str, list[float]]) -> None:
        """Solo sobre símbolos existentes; ignora ids que no están."""

    def leer_embeddings(self, grafo: str, ids: list[str]) -> dict[str, list[float]]:
        """Solo los ids que tienen embedding."""

    def knn(self, grafo: str, vector: list[float], k: int) -> list[tuple[str, float]]:
        """(id, similitud coseno en [-1, 1]) de mayor a menor."""

    # --- analítica ---------------------------------------------------------
    def reemplazar_analitica(self, grafo: str, clusters: list[Cluster], procesos: list[Proceso]) -> None: ...

    def analitica_de(self, grafo: str, simbolo: str) -> tuple[list[Cluster], list[Proceso]]:
        """Clusters y procesos que contienen al símbolo."""

    def procesos(self, grafo: str) -> list[Proceso]: ...

    # --- trazabilidad CA-NN ------------------------------------------------
    def agregar_trazas(self, grafo: str, trazas: list[Traza]) -> None:
        """Idempotente: una traza repetida no se duplica."""

    def trazas(
        self, grafo: str, unidad: str | None, criterio: str | None, simbolo: str | None
    ) -> list[Traza]:
        """Las trazas que cumplen todos los filtros dados (``None`` = sin filtro), ordenadas."""
