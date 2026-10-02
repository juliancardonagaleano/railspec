"""Interfaz de proveedor de modelos.

Los nodos de modelo del DAG (críticos, refutador, redactor delegado) piden una
salida estructurada con un esquema Pydantic y reciben el valor validado más el
uso. Foundry es el proveedor primario; Anthropic entra por la misma interfaz
tras su bandera. El motor nunca importa un SDK de proveedor: solo esta
interfaz.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel
from railspec.contracts.comun import Effort, Proveedor

T = TypeVar("T", bound=BaseModel)


class ErrorProveedor(Exception):
    """El proveedor no devolvió una salida utilizable (red, cuota, rechazo, esquema).

    El gate lo convierte en ``escalado`` con causa ``error-proveedor``: nunca
    aprueba por falta de respuesta. Los adaptadores rellenan ``duracion_ms``
    para que la llamada fallida también quede en la auditoría.
    """

    def __init__(self, mensaje: str, *, duracion_ms: int = 0) -> None:
        super().__init__(mensaje)
        self.duracion_ms = duracion_ms


@dataclass(frozen=True)
class Uso:
    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_cache_lectura: int = 0
    tokens_cache_escritura: int = 0
    costo_usd: float = 0.0
    duracion_ms: int = 0

    def __add__(self, otro: Uso) -> Uso:
        return Uso(
            self.tokens_entrada + otro.tokens_entrada,
            self.tokens_salida + otro.tokens_salida,
            self.tokens_cache_lectura + otro.tokens_cache_lectura,
            self.tokens_cache_escritura + otro.tokens_cache_escritura,
            round(self.costo_usd + otro.costo_usd, 6),
            self.duracion_ms + otro.duracion_ms,
        )

    @property
    def tokens(self) -> int:
        return self.tokens_entrada + self.tokens_salida


@dataclass(frozen=True)
class PeticionModelo(Generic[T]):
    """Una llamada estructurada.

    ``sistema`` va primero y debe ser estable (rúbrica, gobernanza): es el
    prefijo que aprovecha la caché de prompt del proveedor. ``contenido`` es lo
    variable (el artefacto, el diff de iteración).

    ``modelo`` es el id del catálogo (``claude-opus-5-5``) y es lo que se
    audita; ``despliegue`` es el nombre que se envía al proveedor (en Foundry,
    el despliegue). ``region`` es donde corre la inferencia según el catálogo;
    sin ella, la respuesta lleva la región del adaptador.

    ``commits`` (``repositorio@commit``, ordenados) es el estado del código
    sobre el que se evalúa. No viaja al proveedor: solo entra en la clave de la
    caché de nodos, para que una respuesta no se reutilice sobre otro commit.
    """

    rol: str
    modelo: str
    sistema: str
    contenido: str
    esquema: type[T]
    effort: Effort | None = None
    max_tokens: int = 16_000
    etiqueta: str = ""
    metadatos: dict[str, str] = field(default_factory=dict)
    despliegue: str | None = None
    region: str | None = None
    commits: tuple[str, ...] = ()

    @property
    def destino(self) -> str:
        return self.despliegue or self.modelo


@dataclass(frozen=True)
class RespuestaModelo(Generic[T]):
    valor: T
    uso: Uso
    proveedor: Proveedor
    modelo: str
    region: str | None = None


@runtime_checkable
class ProveedorModelo(Protocol):
    proveedor: Proveedor
    #: Región o zona de datos donde corre la inferencia por defecto (auditoría, R4).
    #: ``global`` = sin garantía de zona; nunca sirve a ``restringido`` ni ``interno``.
    region: str | None

    async def completar(self, peticion: PeticionModelo[T]) -> RespuestaModelo[T]: ...


#: Precio por millón de tokens (entrada, salida, lectura de caché) en USD.
#: Tarifas de primera parte, que Foundry factura igual (Marketplace). Solo
#: sirve para presupuestos y telemetría; la factura real manda.
PRECIOS_USD_MTOK: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 1.0),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
}


def costo_estimado(modelo: str, uso: Uso) -> float:
    precio = next((p for nombre, p in PRECIOS_USD_MTOK.items() if modelo.startswith(nombre)), None)
    if precio is None:
        return 0.0
    entrada, salida, cache = precio
    return round(
        (uso.tokens_entrada * entrada + uso.tokens_salida * salida + uso.tokens_cache_lectura * cache) / 1e6,
        6,
    )
