"""Operación del servidor y claves de ``GET /metrics`` (solo quien administra la plataforma).

``GET /operacion`` es lo mismo que publica ``/metrics`` (sondas, versión, esquema, peticiones por superficie
desde el arranque), en JSON y con la sesión de la consola: no hace falta ninguna clave para verlo aquí.
Las claves son para lo que consulta ``/metrics`` desde fuera (Prometheus, Grafana, ``verificar_metricas.py``):
una por origen, el secreto solo sale en la respuesta que la crea, y revocarla la invalida al instante.
Ver ``railspec/docs/consola.md`` § Operación y ``railspec/server/claves_metricas.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from ..claves_metricas import ClaveMetricas, ErrorClaveMetricas
from .api import Entrada
from .contexto import ContextoConsola

log = logging.getLogger("railspec.consola")

router = APIRouter()


async def _plataforma(request: Request) -> tuple[ContextoConsola, str]:
    ctx: ContextoConsola = request.app.state.consola
    sesion = await ctx.sesion(request)
    ctx.permisos(sesion).exigir_plataforma()
    if ctx.operacion is None:
        raise HTTPException(409, "este servidor no publica su operación")
    return ctx, sesion.login


def _almacen(ctx: ContextoConsola) -> Any:
    if ctx.operacion.claves is None:
        raise HTTPException(409, "este servidor no guarda claves de métricas")
    return ctx.operacion.claves


def _vista(c: ClaveMetricas) -> dict[str, Any]:
    return {**c.model_dump(mode="json"), "activa": c.activa}


@router.get("/operacion")
async def operacion(request: Request) -> dict[str, Any]:
    ctx, _ = await _plataforma(request)
    return await ctx.operacion.vista()


@router.get("/metricas/claves")
async def claves(request: Request) -> list[dict[str, Any]]:
    ctx, _ = await _plataforma(request)
    return [_vista(c) for c in await asyncio.to_thread(_almacen(ctx).listar)]


class ClaveNueva(Entrada):
    #: El origen que la usará («Grafana», «verificación manual»): es lo que se ve en la lista.
    nombre: str = Field(min_length=1, max_length=60)


@router.post("/metricas/claves", status_code=201)
async def crear_clave(entrada: ClaveNueva, request: Request) -> dict[str, Any]:
    ctx, login = await _plataforma(request)
    try:
        clave, secreto = await asyncio.to_thread(_almacen(ctx).crear, entrada.nombre, login, ctx.reloj())
    except ErrorClaveMetricas as exc:
        raise HTTPException(exc.estado, exc.detalle) from exc
    log.info("clave de métricas %s («%s») creada por %s", clave.prefijo, clave.nombre, login)
    return {"clave": _vista(clave), "secreto": secreto}


@router.delete("/metricas/claves/{id_}")
async def revocar_clave(id_: str, request: Request) -> dict[str, Any]:
    ctx, login = await _plataforma(request)
    clave = await asyncio.to_thread(_almacen(ctx).revocar, id_, login, ctx.reloj())
    if clave is None:
        raise HTTPException(404, "no existe esa clave de métricas")
    log.info("clave de métricas %s («%s») revocada por %s", clave.prefijo, clave.nombre, login)
    return _vista(clave)
