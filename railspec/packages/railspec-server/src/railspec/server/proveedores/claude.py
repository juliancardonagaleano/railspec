"""Claude por la Messages API con el SDK oficial: Foundry (``/anthropic``) y Anthropic directo.

La forma de la petición es la misma en los dos: sistema estable con
``cache_control`` (prefijo cacheable), salida estructurada con
``output_config.format`` (``messages.parse`` la arma desde el esquema Pydantic)
y ``effort`` en ``output_config``. Sin ``thinking`` explícito (los modelos
actuales corren adaptativo por defecto y algunos rechazan desactivarlo), sin
prefill ni ``tool_choice`` forzado.

El SDK se importa solo al construir el cliente: el motor y sus pruebas no lo
necesitan. Las pruebas inyectan un cliente doble con ``messages.parse``.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError
from railspec.contracts.comun import Proveedor

from .base import ErrorProveedor, PeticionModelo, RespuestaModelo, T, Uso, costo_estimado

#: Scope de Entra ID para el endpoint ``/anthropic`` de Foundry (documentación del SDK).
SCOPE_FOUNDRY_ANTHROPIC = "https://ai.azure.com/.default"

ProveedorToken = Callable[[], "str | Awaitable[str]"]

#: ``stop_reason`` que no dejan una salida utilizable.
_CORTADAS = {"max_tokens", "refusal", "model_context_window_exceeded"}


class AdaptadorClaude:
    """``ProveedorModelo`` sobre un cliente asíncrono de la Messages API (``AsyncAnthropic*``)."""

    def __init__(self, proveedor: Proveedor, cliente: Any, region: str | None = None) -> None:
        self.proveedor = proveedor
        self.region = region
        self._cliente = cliente

    async def completar(self, peticion: PeticionModelo[T]) -> RespuestaModelo[T]:
        opciones: dict[str, Any] = {}
        if peticion.effort is not None:
            opciones["output_config"] = {"effort": peticion.effort.value}
        inicio = time.monotonic()
        try:
            respuesta = await self._cliente.messages.parse(
                model=peticion.destino,
                max_tokens=peticion.max_tokens,
                system=[{"type": "text", "text": peticion.sistema, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": peticion.contenido}],
                output_format=peticion.esquema,
                **opciones,
            )
        except (ValidationError, ValueError) as exc:
            raise ErrorProveedor(
                f"{peticion.destino}: salida fuera de esquema: {exc}", duracion_ms=_ms(inicio)
            ) from exc
        except Exception as exc:  # red, cuota, 4xx/5xx del SDK: todo escala igual
            raise ErrorProveedor(
                f"{self.proveedor.value}/{peticion.destino}: {type(exc).__name__}: {exc}",
                duracion_ms=_ms(inicio),
            ) from exc
        duracion = _ms(inicio)
        motivo = str(getattr(respuesta, "stop_reason", "") or "")
        if motivo in _CORTADAS:
            raise ErrorProveedor(f"{peticion.destino}: respuesta cortada ({motivo})", duracion_ms=duracion)
        valor = getattr(respuesta, "parsed_output", None)
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
        uso = Uso(
            tokens_entrada=_entero(u, "input_tokens"),
            tokens_salida=_entero(u, "output_tokens"),
            tokens_cache_lectura=_entero(u, "cache_read_input_tokens"),
            tokens_cache_escritura=_entero(u, "cache_creation_input_tokens"),
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


def _entero(objeto: Any, campo: str) -> int:
    return int(getattr(objeto, campo, 0) or 0) if objeto is not None else 0


def cliente_foundry(endpoint: str, api_key: str | None, proveedor_token: ProveedorToken | None) -> Any:
    """``AsyncAnthropicFoundry`` sobre ``<endpoint>/anthropic/``; sin clave, el token de Entra ID."""

    from anthropic import AsyncAnthropicFoundry

    base = endpoint.rstrip("/")
    base = base if base.endswith("/anthropic") else f"{base}/anthropic"
    if api_key:
        return AsyncAnthropicFoundry(base_url=f"{base}/", api_key=api_key)
    if proveedor_token is None:
        raise ValueError("Foundry sin API key necesita un proveedor de token de Entra ID")
    return AsyncAnthropicFoundry(base_url=f"{base}/", azure_ad_token_provider=proveedor_token)


def cliente_anthropic(api_key: str, **opciones: Any) -> Any:
    """``AsyncAnthropic``; ``opciones`` (``timeout``, ``max_retries``) para lecturas con tope propio."""

    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=api_key, **opciones)
