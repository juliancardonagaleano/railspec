"""Selección de proveedor y modelo por rol.

Foundry es el primario. Anthropic directo solo entra con su bandera y solo
para repositorios ``abierto``: en ``restringido`` e ``interno`` el contenido
se queda en la zona de datos de Azure (política aprobada el 2026-09-30).
"""

from __future__ import annotations

from dataclasses import dataclass

from railspec.contracts.comun import NivelCodigo, Proveedor
from railspec.contracts.repositorio import RequisitoRol

from .base import ProveedorModelo


class PerfilInsatisfacible(Exception):
    """Ningún proveedor configurado puede servir el rol pedido (``perfil-insatisfacible``)."""


@dataclass(frozen=True)
class Eleccion:
    proveedor: ProveedorModelo
    modelo: str


class Proveedores:
    def __init__(self, disponibles: dict[Proveedor, ProveedorModelo]) -> None:
        self._disponibles = disponibles

    @property
    def configurados(self) -> list[Proveedor]:
        return sorted(self._disponibles)

    def elegir(self, rol: str, requisito: RequisitoRol, nivel: NivelCodigo) -> Eleccion:
        orden = [Proveedor.foundry]
        if nivel == NivelCodigo.abierto:
            orden.append(Proveedor.anthropic)
        for p in orden:
            if p in self._disponibles and p in requisito.modelo:
                return Eleccion(self._disponibles[p], requisito.modelo[p])
        raise PerfilInsatisfacible(
            f"rol {rol}: ningún proveedor configurado ({', '.join(self.configurados) or 'ninguno'}) "
            f"sirve {sorted(requisito.modelo)} en nivel {nivel.value}"
        )
