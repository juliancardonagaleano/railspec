"""Transporte de producción: MCP Streamable HTTP hacia ``railspec-server``.

El token del desarrollador (GitHub OAuth) viaja como ``Authorization:
Bearer``; el servidor deriva el actor de él. La sesión MCP se abre al primer
uso y se reabre si la conexión se cae.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from railspec.contracts.tools import nombre_mcp

from .cliente import error_desde
from .errores import RespuestaInvalida, SinConexion


class TransporteMcpHttp:
    def __init__(self, url: str, token: str | None, timeout_s: float = 300.0) -> None:
        self.url = url
        self.token = token
        self.timeout_s = timeout_s
        self._pila: AsyncExitStack | None = None
        self._cliente: Client | None = None

    async def _abrir(self) -> Client:
        if self._cliente is not None:
            return self._cliente
        cabeceras = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        pila = AsyncExitStack()
        try:
            http = await pila.enter_async_context(
                httpx2.AsyncClient(headers=cabeceras, timeout=httpx2.Timeout(30.0, read=self.timeout_s))
            )
            cliente = await pila.enter_async_context(
                Client(streamable_http_client(self.url, http_client=http))
            )
        except Exception as exc:
            await pila.aclose()
            raise SinConexion(f"No se pudo conectar con {self.url}: {exc}") from exc
        self._pila, self._cliente = pila, cliente
        return cliente

    async def cerrar(self) -> None:
        if self._pila is not None:
            pila, self._pila, self._cliente = self._pila, None, None
            await pila.aclose()

    async def llamar(self, tool: str, argumentos: dict[str, Any]) -> dict[str, Any]:
        cliente = await self._abrir()
        try:
            # El servidor publica cada tool en /mcp con su alias MCP (contrato 1.3).
            resultado = await cliente.call_tool(nombre_mcp(tool), argumentos)
        except (httpx2.TransportError, OSError, ConnectionError) as exc:
            await self.cerrar()
            raise SinConexion(f"Se perdió la conexión con {self.url}: {exc}") from exc
        datos = resultado.structured_content
        if datos is None:
            datos = _json_de_contenido(resultado.content)
        if resultado.is_error:
            raise error_desde(datos if isinstance(datos, dict) else {})
        if not isinstance(datos, dict):
            raise RespuestaInvalida(f"{tool}: el servidor no devolvió un objeto JSON")
        return datos


def _json_de_contenido(contenido: list[Any]) -> Any:
    for bloque in contenido:
        texto = getattr(bloque, "text", None)
        if texto:
            try:
                return json.loads(texto)
            except json.JSONDecodeError:
                continue
    return None
