"""Modelos de Foundry que no son Claude, por chat completions (API v1 de Azure OpenAI).

Para despliegues en zona de datos que sirven a ``restringido`` e ``interno``
cuando el despliegue de Claude es Global. Salida estructurada con
``response_format`` (``chat.completions.parse`` arma el ``json_schema`` desde
el modelo Pydantic) y ``reasoning_effort`` solo si el perfil lo pide; el
catálogo ya comprobó que el modelo lo admite.

El SDK de OpenAI se importa solo al construir el cliente; las pruebas
inyectan un cliente doble con ``chat.completions.parse``.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError
from railspec.contracts.comun import Effort, Proveedor

from .base import ErrorProveedor, PeticionModelo, RespuestaModelo, T, Uso, costo_estimado

#: Scope de Entra ID de la API v1 de Azure OpenAI.
SCOPE_AZURE_OPENAI = "https://cognitiveservices.azure.com/.default"

#: chat completions solo conoce tres niveles; ``xhigh`` y ``max`` no se degradan
#: aquí: la selección ya rechazó el perfil si el catálogo no los admite.
_EFFORT = {Effort.low: "low", Effort.medium: "medium", Effort.high: "high"}


class AdaptadorChatCompletions:
    def __init__(self, proveedor: Proveedor, cliente: Any, region: str | None = None) -> None:
        self.proveedor = proveedor
        self.region = region
        self._cliente = cliente

    async def completar(self, peticion: PeticionModelo[T]) -> RespuestaModelo[T]:
        opciones: dict[str, Any] = {}
        if peticion.effort is not None:
            if peticion.effort not in _EFFORT:
                raise ErrorProveedor(f"{peticion.destino}: effort {peticion.effort.value} no existe en chat")
            opciones["reasoning_effort"] = _EFFORT[peticion.effort]
        inicio = time.monotonic()
        try:
            respuesta = await self._cliente.chat.completions.parse(
                model=peticion.destino,
                messages=[
                    {"role": "system", "content": peticion.sistema},
                    {"role": "user", "content": peticion.contenido},
                ],
                response_format=peticion.esquema,
                max_completion_tokens=peticion.max_tokens,
                **opciones,
            )
        except (ValidationError, ValueError) as exc:
            raise ErrorProveedor(
                f"{peticion.destino}: salida fuera de esquema: {exc}", duracion_ms=_ms(inicio)
            ) from exc
        except Exception as exc:  # red, cuota, filtro de contenido, 4xx/5xx
            raise ErrorProveedor(
                f"{self.proveedor.value}/{peticion.destino}: {type(exc).__name__}: {exc}",
                duracion_ms=_ms(inicio),
            ) from exc
        duracion = _ms(inicio)
        eleccion = (getattr(respuesta, "choices", None) or [None])[0]
        if eleccion is None:
            raise ErrorProveedor(f"{peticion.destino}: respuesta sin choices", duracion_ms=duracion)
        motivo = str(getattr(eleccion, "finish_reason", "") or "")
        if motivo in ("length", "content_filter"):
            raise ErrorProveedor(f"{peticion.destino}: respuesta cortada ({motivo})", duracion_ms=duracion)
        mensaje = eleccion.message
        if getattr(mensaje, "refusal", None):
            raise ErrorProveedor(f"{peticion.destino}: rechazo del modelo", duracion_ms=duracion)
        valor = getattr(mensaje, "parsed", None)
        if valor is None:
            raise ErrorProveedor(
                f"{peticion.destino}: respuesta sin salida estructurada", duracion_ms=duracion
            )
        if not isinstance(valor, peticion.esquema):
            try:
                valor = peticion.esquema.model_validate(valor)
            except ValidationError as exc:
                raise ErrorProveedor(f"salida fuera de esquema: {exc}", duracion_ms=duracion) from exc
        u = getattr(respuesta, "usage", None)
        detalle = getattr(u, "prompt_tokens_details", None)
        cache = int(getattr(detalle, "cached_tokens", 0) or 0)
        entrada = int(getattr(u, "prompt_tokens", 0) or 0)
        uso = Uso(
            # Mismo criterio que la Messages API: entrada sin lo leído de caché.
            tokens_entrada=max(entrada - cache, 0),
            tokens_salida=int(getattr(u, "completion_tokens", 0) or 0),
            tokens_cache_lectura=cache,
            duracion_ms=duracion,
        )
        uso = Uso(**{**uso.__dict__, "costo_usd": costo_estimado(peticion.modelo, uso)})
        return RespuestaModelo(
            valor=valor,
            uso=uso,
            proveedor=self.proveedor,
            modelo=peticion.modelo,
            region=peticion.region or self.region,
        )


def _ms(inicio: float) -> int:
    return int((time.monotonic() - inicio) * 1000)


def cliente_azure_openai(
    endpoint: str, api_key: str | None, proveedor_token: Callable[[], Awaitable[str]] | None
) -> Any:
    """``AsyncOpenAI`` sobre ``<endpoint>/openai/v1/`` con API key o token de Entra ID."""

    from openai import AsyncOpenAI

    base = endpoint.rstrip("/").removesuffix("/anthropic")
    credencial: Any = api_key or proveedor_token
    if credencial is None:
        raise ValueError("Foundry sin API key necesita un proveedor de token de Entra ID")
    return AsyncOpenAI(base_url=f"{base}/openai/v1/", api_key=credencial)
