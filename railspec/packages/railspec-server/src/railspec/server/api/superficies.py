"""Superficies MCP y HTTP generadas desde el registro único de tools.

- MCP (streamable HTTP en ``/mcp``): lo consume el proxy local; el actor sale
  del ``Authorization: Bearer`` con canal ``arnes``. Errores de negocio como
  ``isError`` con el ``ErrorTool`` en ``structuredContent``.
- HTTP (``POST /v1/tools/{nombre}``): lo consume la consola, canal ``consola``.
  ``GET /v1/tools`` publica el manifiesto de las tools HTTP disponibles.
- ``GET /healthz`` (disponibilidad): 200 si cada sonda (Mongo, FalkorDB)
  responde dentro del tope, 503 si no. ``GET /livez`` (vida): solo que el
  proceso atiende; no reinicia la réplica por una caída de la base.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from railspec.contracts.comun import Actor
from railspec.contracts.tools import Superficie

from .identidad import TokenInvalido, token_de_cabecera
from .registro import Registro


def _actor(identidad: Any, autorizacion: str | None, canal: str) -> Actor:
    token = token_de_cabecera(autorizacion)
    if token is None:
        raise TokenInvalido("falta Authorization: Bearer")
    return identidad.actor_desde_token(token, canal)


def servidor_mcp(registro: Registro, identidad: Any):
    import mcp_types as types
    from mcp.server.lowlevel import Server

    async def listar(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=t.nombre_mcp,
                    description=t.descripcion,
                    input_schema=t.entrada.model_json_schema(),
                    output_schema=t.salida.model_json_schema(),
                )
                for t in registro.tools(Superficie.mcp)
            ]
        )

    async def llamar(ctx, params) -> types.CallToolResult:
        cabecera = ctx.request.headers.get("authorization") if ctx.request is not None else None
        try:
            actor = _actor(identidad, cabecera, "arnes")
        except TokenInvalido as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], is_error=True
            )
        r = await registro.invocar(params.name, params.arguments or {}, actor, Superficie.mcp)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(r.cuerpo, ensure_ascii=False))],
            structured_content=r.cuerpo,
            is_error=not r.ok,
        )

    return Server("railspec", version="0.1.0", on_list_tools=listar, on_call_tool=llamar)


TOPE_SONDA_S = 2.0


async def _sondear(sondas: dict[str, Callable[[], Any]], tope_s: float) -> dict[str, str]:
    async def una(sonda: Callable[[], Any]) -> str:
        try:
            await asyncio.wait_for(asyncio.to_thread(sonda), tope_s)
            return "ok"
        except TimeoutError:
            return "sin respuesta"
        except Exception as exc:
            return f"error: {type(exc).__name__}"

    nombres = list(sondas)
    resultados = await asyncio.gather(*(una(sondas[n]) for n in nombres))
    return dict(zip(nombres, resultados, strict=True))


def aplicacion(
    registro: Registro,
    identidad: Any,
    *,
    host: str = "0.0.0.0",
    sondas: dict[str, Callable[[], Any]] | None = None,
    tope_sonda_s: float = TOPE_SONDA_S,
):
    """App ASGI con la API HTTP de la consola y el MCP montado en ``/mcp``."""

    mcp_app = servidor_mcp(registro, identidad).streamable_http_app(streamable_http_path="/", host=host)

    @contextlib.asynccontextmanager
    async def vida(app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(title="Railspec", version="0.1.0", lifespan=vida)

    @app.get("/livez")
    async def vida() -> dict[str, str]:
        return {"estado": "ok"}

    @app.get("/healthz")
    async def salud() -> JSONResponse:
        resultados = await _sondear(sondas or {}, tope_sonda_s)
        sano = all(v == "ok" for v in resultados.values())
        return JSONResponse(
            {"estado": "ok" if sano else "degradado", **resultados}, status_code=200 if sano else 503
        )

    @app.get("/v1/tools")
    async def manifiesto() -> dict[str, Any]:
        return {"tools": [t.manifiesto() for t in registro.tools(Superficie.http)]}

    @app.post("/v1/tools/{nombre}")
    async def invocar(nombre: str, request: Request) -> JSONResponse:
        try:
            actor = _actor(identidad, request.headers.get("authorization"), "consola")
        except TokenInvalido as exc:
            return JSONResponse({"detalle": str(exc)}, status_code=401)
        try:
            argumentos = await request.json()
        except ValueError:
            return JSONResponse({"detalle": "cuerpo JSON inválido"}, status_code=422)
        r = await registro.invocar(nombre, argumentos, actor, Superficie.http)
        return JSONResponse(r.cuerpo, status_code=r.estado_http)

    app.mount("/mcp", mcp_app)
    return app
