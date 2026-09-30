"""Adaptadores de proveedor sobre los clientes de chat de Microsoft Agent Framework.

Foundry (primario): los modelos Claude alojados en Azure se invocan por el
endpoint ``/anthropic`` del recurso con ``AnthropicFoundryClient``; con API key
o, sin ella, con Entra ID. Anthropic directo usa ``AnthropicClient`` y solo se
construye con ``RAILSPEC_ANTHROPIC_HABILITADO``. Los dos comparten la misma
forma de petición: sistema estable con ``cache_control`` (prefijo cacheable),
salida estructurada por ``output_config.format`` (MAF la arma desde
``response_format``) y ``effort`` en ``output_config``. Sin prefill ni
``tool_choice`` forzado: los modelos actuales los rechazan.

Los SDK se importan solo al construir el cliente, así que el motor y sus
pruebas no los necesitan.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError
from railspec.contracts.comun import Proveedor

from .base import ErrorProveedor, PeticionModelo, RespuestaModelo, T, Uso, costo_estimado

FabricaCliente = Callable[[str], Any]
"""Recibe el modelo (o despliegue) y devuelve un cliente de chat de MAF."""


class AdaptadorChatMAF:
    """``ProveedorModelo`` sobre cualquier ``SupportsChatGetResponse`` de MAF."""

    def __init__(self, proveedor: Proveedor, fabrica: FabricaCliente, region: str | None = None) -> None:
        self.proveedor = proveedor
        self.region = region
        self._fabrica = fabrica
        self._clientes: dict[str, Any] = {}

    def _cliente(self, modelo: str) -> Any:
        if modelo not in self._clientes:
            self._clientes[modelo] = self._fabrica(modelo)
        return self._clientes[modelo]

    async def completar(self, peticion: PeticionModelo[T]) -> RespuestaModelo[T]:
        from agent_framework import Message

        opciones: dict[str, Any] = {
            "instructions": [
                {"type": "text", "text": peticion.sistema, "cache_control": {"type": "ephemeral"}}
            ],
            "response_format": peticion.esquema,
            "max_tokens": peticion.max_tokens,
        }
        if peticion.effort is not None:
            opciones["output_config"] = {"effort": peticion.effort.value}
        inicio = time.monotonic()
        try:
            respuesta = await self._cliente(peticion.modelo).get_response(
                [Message("user", [peticion.contenido])], options=opciones
            )
        except Exception as exc:  # red, cuota, 4xx/5xx: todo escala igual
            raise ErrorProveedor(f"{self.proveedor.value}/{peticion.modelo}: {exc}") from exc
        duracion = int((time.monotonic() - inicio) * 1000)
        if str(getattr(respuesta, "finish_reason", "") or "") in ("content_filter", "length"):
            raise ErrorProveedor(f"{peticion.modelo}: respuesta cortada ({respuesta.finish_reason})")
        valor = _valor(respuesta, peticion.esquema)
        detalle = getattr(respuesta, "usage_details", None) or {}
        uso = Uso(
            tokens_entrada=int(detalle.get("input_token_count") or 0),
            tokens_salida=int(detalle.get("output_token_count") or 0),
            tokens_cache_lectura=int(detalle.get("cache_read_input_token_count") or 0),
            tokens_cache_escritura=int(detalle.get("cache_creation_input_token_count") or 0),
            duracion_ms=duracion,
        )
        uso = Uso(**{**uso.__dict__, "costo_usd": costo_estimado(peticion.modelo, uso)})
        return RespuestaModelo(
            valor=valor, uso=uso, proveedor=self.proveedor, modelo=peticion.modelo, region=self.region
        )


def _valor(respuesta: Any, esquema: type[BaseModel]) -> Any:
    try:
        valor = respuesta.value
    except (ValidationError, ValueError) as exc:
        raise ErrorProveedor(f"salida fuera de esquema: {exc}") from exc
    if valor is None:
        try:
            valor = esquema.model_validate_json(respuesta.text)
        except (ValidationError, ValueError) as exc:
            raise ErrorProveedor(f"salida fuera de esquema: {exc}") from exc
    if not isinstance(valor, esquema):
        valor = esquema.model_validate(valor)
    return valor


def adaptador_foundry(endpoint: str, api_key: str | None, region: str | None = None) -> AdaptadorChatMAF:
    """Claude en Azure Foundry por ``<endpoint>/anthropic``; sin clave, Entra ID."""

    def fabrica(modelo: str) -> Any:
        from agent_framework_anthropic import AnthropicFoundryClient

        base = endpoint if endpoint.endswith("/anthropic") else f"{endpoint}/anthropic"
        if api_key:
            return AnthropicFoundryClient(model=modelo, base_url=base, api_key=api_key)
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        proveedor_token = get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
        )
        return AnthropicFoundryClient(model=modelo, base_url=base, azure_ad_token_provider=proveedor_token)

    return AdaptadorChatMAF(Proveedor.foundry, fabrica, region=region)


def adaptador_anthropic(api_key: str) -> AdaptadorChatMAF:
    def fabrica(modelo: str) -> Any:
        from agent_framework_anthropic import AnthropicClient

        return AnthropicClient(model=modelo, api_key=api_key)

    return AdaptadorChatMAF(Proveedor.anthropic, fabrica, region=None)
