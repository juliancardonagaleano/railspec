"""Transporte de producción: MCP Streamable HTTP hacia ``railspec-server``.

El token del desarrollador (token de usuario de la GitHub App de Railspec) viaja
como ``Authorization: Bearer``; el servidor deriva el actor de él. Sale de
``RAILSPEC_TOKEN`` o de la sesión que guardó ``railspec login`` y se resuelve en
cada petición, no al abrir la sesión MCP: un ``railspec login`` con el arnés
abierto vale sin reiniciarlo, y una sesión vencida se renueva sola con su refresh
token (``renovacion.py``). La sesión MCP se abre al primer uso, en una tarea
propia, y se reabre si la conexión se cae.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from railspec.contracts.tools import nombre_mcp

from .cliente import error_desde
from .credenciales import FuenteToken
from .errores import RespuestaInvalida, ServidorRechazo, SinConexion


class _BearerVigente(httpx2.Auth):
    """Pone el token que valga ahora en cada petición, incluidas las de reconexión."""

    def __init__(self, fuente: FuenteToken) -> None:
        self.fuente = fuente

    def auth_flow(self, request: httpx2.Request):
        token = self.fuente.token()
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        yield request

    async def async_auth_flow(self, request: httpx2.Request):
        # ``token()`` lee el archivo y, con la sesión vencida, renueva por red (candado de archivo, hasta
        # unos segundos): en un hilo, para no parar el bucle del proxy.
        token = await asyncio.to_thread(self.fuente.token)
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        yield request


class TransporteMcpHttp:
    """La sesión MCP vive en una tarea propia.

    El proxy atiende cada tool del arnés en una tarea distinta y los contextos del
    cliente MCP (grupos de tareas de anyio) deben cerrarse en la misma tarea que los
    abrió: si la sesión se abriera dentro de la primera tool, al terminar esa tool
    anyio rompería con "cancel scope" y el arnés perdería la conexión."""

    def __init__(self, url: str, token: str | FuenteToken | None, timeout_s: float = 300.0) -> None:
        self.url = url
        # Un ``str`` es un token fijo (como ``RAILSPEC_TOKEN``); la CLI pasa la fuente completa.
        self.fuente = token if isinstance(token, FuenteToken) else FuenteToken(url, token)
        self.timeout_s = timeout_s
        self._cliente: Client | None = None
        self._tarea: asyncio.Task[None] | None = None
        self._listo: asyncio.Future[Client] | None = None
        self._fin: asyncio.Event | None = None

    async def _abrir(self) -> Client:
        if self._cliente is not None:
            return self._cliente
        if self._listo is None or (self._listo.done() and self._cliente is None):
            # Una sola apertura aunque lleguen varias tools a la vez; la primera la lanza.
            self._listo = asyncio.get_running_loop().create_future()
            self._fin = asyncio.Event()
            self._tarea = asyncio.create_task(self._sostener(self._listo, self._fin))
        return await asyncio.shield(self._listo)

    async def _sostener(self, listo: asyncio.Future[Client], fin: asyncio.Event) -> None:
        try:
            async with (
                httpx2.AsyncClient(
                    auth=_BearerVigente(self.fuente), timeout=httpx2.Timeout(30.0, read=self.timeout_s)
                ) as http,
                Client(streamable_http_client(self.url, http_client=http)) as cliente,
            ):
                self._cliente = cliente
                listo.set_result(cliente)
                await fin.wait()
        except Exception as exc:
            if not listo.done():
                listo.set_exception(SinConexion(f"No se pudo conectar con {self.url}: {_causa(exc)}"))
        finally:
            if self._listo is listo:
                self._cliente = None
            if not listo.done():
                # Cancelada antes de abrir: quien espera no se queda colgado.
                listo.set_exception(SinConexion(f"Se canceló la conexión con {self.url}"))

    async def cerrar(self) -> None:
        tarea, fin = self._tarea, self._fin
        self._cliente = self._tarea = self._listo = self._fin = None
        if tarea is not None and fin is not None:
            fin.set()
            with contextlib.suppress(Exception):
                await tarea

    async def herramientas(self) -> list[str]:
        """Nombres de las tools que publica el servidor; no exige sesión ni cambia nada."""

        cliente = await self._abrir()
        nombres: list[str] = []
        cursor: str | None = None
        try:
            while True:
                pagina = await cliente.list_tools(cursor=cursor)
                nombres += [t.name for t in pagina.tools]
                cursor = pagina.next_cursor
                if not cursor:
                    return nombres
        except (httpx2.TransportError, OSError, ConnectionError) as exc:
            await self.cerrar()
            raise SinConexion(f"Se perdió la conexión con {self.url}: {exc}") from exc

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
            if isinstance(datos, dict) and "codigo" in datos:
                raise error_desde(datos)
            # Sin ``ErrorTool``: el servidor rechazó la identidad (texto plano), no la tool.
            raise ServidorRechazo(tool, _texto_de_contenido(resultado.content), self.fuente.nota())
        if not isinstance(datos, dict):
            raise RespuestaInvalida(f"{tool}: el servidor no devolvió un objeto JSON")
        return datos


def _causa(exc: BaseException) -> str:
    """El error de fondo de una conexión fallida: anyio lo envuelve en un grupo sin texto útil."""

    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    texto = str(exc)
    return f"{type(exc).__name__}: {texto}" if texto else type(exc).__name__


def _texto_de_contenido(contenido: list[Any]) -> str:
    for bloque in contenido:
        texto = getattr(bloque, "text", None)
        if texto:
            return str(texto).strip()[:500]
    return ""


def _json_de_contenido(contenido: list[Any]) -> Any:
    for bloque in contenido:
        texto = getattr(bloque, "text", None)
        if texto:
            try:
                return json.loads(texto)
            except json.JSONDecodeError:
                continue
    return None
