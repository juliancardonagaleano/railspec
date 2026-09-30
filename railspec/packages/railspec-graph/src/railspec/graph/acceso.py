"""Único módulo de acceso a datos del grafo: aquí se impone el filtro de workspace.

Nadie más construye nombres de grafo ni habla con el motor. El resto del
paquete recibe un ``Espacio``, atado a un (org, workspace, repositorio) y,
si aplica, a la superposición de una unidad; un ``Espacio`` no puede
consultar nada fuera de su grafo físico.

Reglas que se imponen aquí y no en los llamadores:

- Toda operación lleva su alcance tipado (``AlcanceRepositorio``).
- Una consulta de varios repositorios solo cruza repositorios del mismo
  workspace y solo los que el servidor calculó como visibles para el actor
  (desde los vínculos y roles); cualquier otro alcance es un error, no un
  resultado vacío.
- Borrar un repositorio borra su grafo canónico, sus superposiciones y sus
  lotes de indexado a medio llegar.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from railspec.contracts.comun import AlcanceRepositorio, AlcanceWorkspace
from railspec.contracts.repositorio import nombre_grafo

from .motor import AristaMotor, Cluster, Meta, MotorGrafo, Proceso

_UNIDAD = re.compile(r"^[0-9]{4}-[a-z0-9][a-z0-9-]{0,62}$")
#: Separador de la superposición de una unidad dentro del espacio del repositorio.
SEPARADOR_SUPERPOSICION = ":u:"
#: Separador del grafo de preparación de ``graph.index`` (lotes de un commit aún incompleto).
SEPARADOR_INDEXADO = ":i:"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class FueraDeWorkspace(PermissionError):
    """Se pidió un repositorio de otro workspace u organización."""


class RepositorioNoVisible(PermissionError):
    """El repositorio pedido no está entre los visibles para el actor."""


@dataclass(frozen=True)
class Espacio:
    """Un grafo físico ya resuelto. Sus métodos no aceptan otro nombre de grafo."""

    alcance: AlcanceRepositorio
    unidad: str | None
    _motor: MotorGrafo
    _grafo: str

    @property
    def es_superposicion(self) -> bool:
        return self.unidad is not None

    def existe(self) -> bool:
        return self._motor.existe(self._grafo)

    def borrar(self) -> None:
        self._motor.borrar(self._grafo)

    def meta(self) -> Meta:
        return self._motor.leer_meta(self._grafo)

    def fijar_meta(self, meta: Meta) -> None:
        self._motor.escribir_meta(self._grafo, meta)

    def upsert_simbolos(self, simbolos: list[dict]) -> None:
        self._motor.upsert_simbolos(self._grafo, simbolos)

    def borrar_simbolos(self, ids: list[str]) -> None:
        self._motor.borrar_simbolos(self._grafo, ids)

    def agregar_aristas(self, aristas: list[AristaMotor]) -> None:
        self._motor.agregar_aristas(self._grafo, aristas)

    def borrar_aristas(self, aristas: list[AristaMotor]) -> None:
        self._motor.borrar_aristas(self._grafo, aristas)

    def simbolos(self, ids: list[str]) -> dict[str, dict]:
        return self._motor.simbolos(self._grafo, ids)

    def buscar_nombre(self, texto: str, tipos: list[str], exacto: bool, limite: int) -> list[dict]:
        return self._motor.buscar_nombre(self._grafo, texto, tipos, exacto, limite)

    def aristas(self, ids: list[str], relaciones: list[str], direccion: str) -> list[AristaMotor]:
        return self._motor.aristas(self._grafo, ids, relaciones, direccion)

    def todos_simbolos(self) -> list[dict]:
        return self._motor.todos_simbolos(self._grafo)

    def todas_aristas(self) -> list[AristaMotor]:
        return self._motor.todas_aristas(self._grafo)

    def fijar_embeddings(self, vectores: dict[str, list[float]]) -> None:
        self._motor.fijar_embeddings(self._grafo, vectores)

    def leer_embeddings(self, ids: list[str]) -> dict[str, list[float]]:
        return self._motor.leer_embeddings(self._grafo, ids)

    def knn(self, vector: list[float], k: int) -> list[tuple[str, float]]:
        return self._motor.knn(self._grafo, vector, k)

    def reemplazar_analitica(self, clusters: list[Cluster], procesos: list[Proceso]) -> None:
        self._motor.reemplazar_analitica(self._grafo, clusters, procesos)

    def analitica_de(self, simbolo: str) -> tuple[list[Cluster], list[Proceso]]:
        return self._motor.analitica_de(self._grafo, simbolo)

    def procesos(self) -> list[Proceso]:
        return self._motor.procesos(self._grafo)


class AccesoGrafo:
    def __init__(self, motor: MotorGrafo) -> None:
        self._motor = motor

    @classmethod
    def desde_entorno(cls) -> AccesoGrafo:
        """FalkorDB desde ``RAILSPEC_FALKORDB_URL`` (Kubernetes Secret en AKS)."""

        url = os.environ.get("RAILSPEC_FALKORDB_URL")
        if not url:
            raise RuntimeError("falta RAILSPEC_FALKORDB_URL")
        from .motor_falkordb import MotorFalkor

        return cls(MotorFalkor.desde_url(url))

    def espacio(self, alcance: AlcanceRepositorio, unidad: str | None = None) -> Espacio:
        grafo = nombre_grafo(alcance)
        if unidad is not None:
            if not _UNIDAD.match(unidad):
                raise ValueError(f"unidad inválida: {unidad!r}")
            grafo += SEPARADOR_SUPERPOSICION + unidad
        return Espacio(alcance, unidad, self._motor, grafo)

    def visibles(
        self, alcance: AlcanceWorkspace, visibles: list[AlcanceRepositorio], pedidos: list[str]
    ) -> list[AlcanceRepositorio]:
        """Filtra los repositorios de una consulta. Único punto donde se cruzan repositorios.

        ``visibles`` lo calcula el servidor; ``pedidos`` vacío = todos los visibles.
        """

        for v in visibles:
            if (v.org, v.workspace) != (alcance.org, alcance.workspace):
                raise FueraDeWorkspace(
                    f"{v.org}/{v.workspace}/{v.repositorio} no es de {alcance.org}/{alcance.workspace}"
                )
        por_slug = {v.repositorio: v for v in visibles}
        if not pedidos:
            return [por_slug[s] for s in sorted(por_slug)]
        faltan = sorted(set(pedidos) - set(por_slug))
        if faltan:
            raise RepositorioNoVisible(", ".join(faltan))
        return [por_slug[s] for s in sorted(set(pedidos))]

    def espacio_indexado(self, alcance: AlcanceRepositorio, commit: str) -> Espacio:
        """Grafo de preparación donde se acumulan los lotes de ``graph.index`` de un commit."""

        if not _COMMIT.match(commit):
            raise ValueError(f"commit inválido: {commit!r}")
        return Espacio(alcance, None, self._motor, nombre_grafo(alcance) + SEPARADOR_INDEXADO + commit)

    def indexados(self, alcance: AlcanceRepositorio) -> list[str]:
        prefijo = nombre_grafo(alcance) + SEPARADOR_INDEXADO
        return [n[len(prefijo) :] for n in self._motor.listar(prefijo)]

    def superposiciones(self, alcance: AlcanceRepositorio) -> list[str]:
        prefijo = nombre_grafo(alcance) + SEPARADOR_SUPERPOSICION
        return [n[len(prefijo) :] for n in self._motor.listar(prefijo)]

    def borrar_repositorio(self, alcance: AlcanceRepositorio) -> None:
        for unidad in self.superposiciones(alcance):
            self.espacio(alcance, unidad).borrar()
        for commit in self.indexados(alcance):
            self.espacio_indexado(alcance, commit).borrar()
        self.espacio(alcance).borrar()
