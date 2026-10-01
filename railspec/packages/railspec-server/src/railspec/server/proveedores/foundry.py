"""Proveedor Azure Foundry: un recurso, dos APIs según el modelo del despliegue.

Los despliegues de Claude van por ``/anthropic`` (Messages API) y el resto por
chat completions (``/openai/v1``). Los dos comparten endpoint, credencial y
región del recurso, y se auditan como ``foundry``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from railspec.contracts.comun import Proveedor

from .base import PeticionModelo, ProveedorModelo, RespuestaModelo, T
from .chat_completions import SCOPE_AZURE_OPENAI, AdaptadorChatCompletions, cliente_azure_openai
from .claude import SCOPE_FOUNDRY_ANTHROPIC, AdaptadorClaude, cliente_foundry


def es_claude(modelo: str) -> bool:
    return modelo.lower().startswith("claude")


class ProveedorFoundry:
    proveedor = Proveedor.foundry

    def __init__(self, claude: ProveedorModelo, chat: ProveedorModelo, region: str | None) -> None:
        self.region = region
        self._claude = claude
        self._chat = chat

    async def completar(self, peticion: PeticionModelo[T]) -> RespuestaModelo[T]:
        destino = self._claude if es_claude(peticion.modelo) else self._chat
        return await destino.completar(peticion)


def proveedor_token_entra(scope: str) -> Callable[[], Awaitable[str]]:
    """Token de Entra ID con ``DefaultAzureCredential`` (identidad de carga de trabajo en AKS)."""

    from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

    return get_bearer_token_provider(DefaultAzureCredential(), scope)


def proveedor_foundry(
    endpoint: str,
    api_key: str | None,
    region: str | None,
    *,
    cliente_claude: Any | None = None,
    cliente_chat: Any | None = None,
) -> ProveedorFoundry:
    if cliente_claude is None:
        token = None if api_key else proveedor_token_entra(SCOPE_FOUNDRY_ANTHROPIC)
        cliente_claude = cliente_foundry(endpoint, api_key, token)
    if cliente_chat is None:
        token = None if api_key else proveedor_token_entra(SCOPE_AZURE_OPENAI)
        cliente_chat = cliente_azure_openai(endpoint, api_key, token)
    return ProveedorFoundry(
        AdaptadorClaude(Proveedor.foundry, cliente_claude, region),
        AdaptadorChatCompletions(Proveedor.foundry, cliente_chat, region),
        region,
    )
