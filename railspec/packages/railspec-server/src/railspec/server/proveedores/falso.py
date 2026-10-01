"""Proveedor guionado para pruebas y desarrollo sin credenciales."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel
from railspec.contracts.comun import Proveedor

from .base import ErrorProveedor, PeticionModelo, RespuestaModelo, T, Uso

USO_FIJO = Uso(tokens_entrada=1000, tokens_salida=200, costo_usd=0.01, duracion_ms=5)

Guion = Callable[[PeticionModelo], BaseModel | Exception]


class ProveedorGuionado:
    """Responde con lo que diga el guion y registra cada petición."""

    def __init__(
        self,
        guion: Guion,
        proveedor: Proveedor = Proveedor.foundry,
        region: str | None = "eastus2",
        uso: Uso = USO_FIJO,
    ) -> None:
        self.proveedor = proveedor
        self.region = region
        self.peticiones: list[PeticionModelo] = []
        self._guion = guion
        self._uso = uso

    async def completar(self, peticion: PeticionModelo[T]) -> RespuestaModelo[T]:
        self.peticiones.append(peticion)
        resultado = self._guion(peticion)
        if isinstance(resultado, Exception):
            raise resultado if isinstance(resultado, ErrorProveedor) else ErrorProveedor(str(resultado))
        valor = peticion.esquema.model_validate(resultado.model_dump())
        return RespuestaModelo(
            valor=valor,
            uso=self._uso,
            proveedor=self.proveedor,
            modelo=peticion.modelo,
            region=peticion.region or self.region,
        )
