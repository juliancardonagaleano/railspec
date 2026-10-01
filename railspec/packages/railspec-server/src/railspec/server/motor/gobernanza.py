"""Proveedor de gobernanza del gate.

El gate nunca critica de memoria: si la gobernanza no respondió (``no``) o
respondió a medias (``parcial``), escala con ``sin-gobernanza`` antes de gastar
un solo token de modelo. La implementación de producción es
``railspec.server.contexto.ContextoConectable`` (PCE y demás herramientas de
contexto por rol); sin ningún proveedor de gobernanza resuelto todo gate
escala: es deliberado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from railspec.contracts.comun import AlcanceUnidad, GateFase, GobernanzaConsultada
from railspec.contracts.orden import ItemGobernanza


@dataclass(frozen=True)
class ResultadoGobernanza:
    consultada: GobernanzaConsultada
    items: list[ItemGobernanza] = field(default_factory=list)
    detalle: str = ""


@runtime_checkable
class ProveedorGobernanza(Protocol):
    async def consultar(self, alcance: AlcanceUnidad, fase: GateFase, objeto: str) -> ResultadoGobernanza: ...


class GobernanzaNoConfigurada:
    async def consultar(self, alcance: AlcanceUnidad, fase: GateFase, objeto: str) -> ResultadoGobernanza:
        return ResultadoGobernanza(GobernanzaConsultada.no, detalle="proveedor de gobernanza no configurado")


class GobernanzaFija:
    """Respuesta fija: pruebas y workspaces que declaran gobernanza vacía a propósito."""

    def __init__(self, items: list[ItemGobernanza] | None = None, consultada=GobernanzaConsultada.si) -> None:
        self._r = ResultadoGobernanza(consultada, list(items or []))

    async def consultar(self, alcance: AlcanceUnidad, fase: GateFase, objeto: str) -> ResultadoGobernanza:
        return self._r
