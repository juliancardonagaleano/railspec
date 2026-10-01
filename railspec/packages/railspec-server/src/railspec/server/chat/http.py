"""Rutas HTTP del chat (``/v1/chat``) que consume ``railspec-console/src/chat``.

Misma identidad que ``/v1/tools``: ``Authorization: Bearer`` resuelto por la
identidad compuesta con canal ``consola`` (token de la consola, de GitHub o
de desarrollo). Una pregunta responde con ``text/event-stream``: ``pregunta``,
``progreso`` (solo el nombre de cada tool, nunca sus argumentos ni
resultados), ``respuesta`` (ya filtrada por el gate de salida), ``error`` y
``fin`` (con la conversación actualizada: consumo de fuga, bloqueos).

No hay streaming de tokens del modelo: el gate necesita la respuesta entera
antes de dejar salir nada.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from railspec.contracts.comun import AlcanceWorkspace, Slug

from ..api.identidad import TokenInvalido
from ..api.superficies import _actor
from .servicio import ErrorChat, Evento, InsumoBloqueado, ServicioChat


class CrearConversacion(BaseModel):
    alcance: AlcanceWorkspace
    repositorios: list[Slug] = Field(default_factory=list, description="Vacío = todos los vinculados.")


class Pregunta(BaseModel):
    pregunta: str = Field(min_length=1, max_length=8000)


class Conservar(BaseModel):
    conservar_en_insumo: bool


class ExportarInsumo(BaseModel):
    objetivo: str = Field(min_length=1, max_length=600)
    restricciones: list[str] = Field(default_factory=list, max_length=20)
    preguntas_abiertas: list[str] = Field(default_factory=list, max_length=20)


def _sse(evento: Evento) -> bytes:
    return f"event: {evento.nombre}\ndata: {json.dumps(evento.datos, ensure_ascii=False)}\n\n".encode()


def _json(modelo: Any) -> Any:
    return json.loads(modelo.model_dump_json())


def router_chat(servicio: ServicioChat, identidad: Any) -> APIRouter:
    router = APIRouter(prefix="/v1/chat")

    def error(exc: ErrorChat) -> JSONResponse:
        cuerpo: dict[str, Any] = {"codigo": exc.codigo, "detalle": exc.detalle}
        if isinstance(exc, InsumoBloqueado):
            cuerpo["reglas_fallidas"] = exc.reglas
        return JSONResponse(cuerpo, status_code=exc.estado_http)

    async def entrada(request: Request, modelo: type[BaseModel]) -> BaseModel:
        try:
            return modelo.model_validate(await request.json())
        except ValueError as exc:  # JSON inválido o ValidationError
            errores = exc.errors() if isinstance(exc, ValidationError) else []
            detalle = [{"ruta": ".".join(map(str, e["loc"])), "mensaje": e["msg"]} for e in errores][:20]
            raise ErrorChat(
                "entrada-invalida", json.dumps(detalle, ensure_ascii=False) or "JSON inválido", 422
            ) from exc

    def actor(request: Request):
        return _actor(identidad, request.headers.get("authorization"), "consola")

    async def atender(request: Request, accion) -> Any:
        try:
            return await accion(actor(request))
        except TokenInvalido as exc:
            return JSONResponse({"detalle": str(exc)}, status_code=401)
        except ErrorChat as exc:
            return error(exc)

    @router.post("/conversaciones")
    async def crear(request: Request):
        async def accion(a):
            datos = await entrada(request, CrearConversacion)
            conv = servicio.crear(a, datos.alcance, list(datos.repositorios))
            return JSONResponse({"conversacion": _json(conv)}, status_code=201)

        return await atender(request, accion)

    @router.get("/conversaciones/{id_}")
    async def obtener(id_: UUID, request: Request):
        async def accion(a):
            conv, mensajes = servicio.obtener(a, id_)
            return {"conversacion": _json(conv), "mensajes": [_json(m) for m in mensajes]}

        return await atender(request, accion)

    @router.post("/conversaciones/{id_}/mensajes")
    async def preguntar(id_: UUID, request: Request):
        async def accion(a):
            datos = await entrada(request, Pregunta)
            flujo = await servicio.preguntar(a, id_, datos.pregunta)

            async def cuerpo() -> AsyncIterator[bytes]:
                async for evento in flujo:
                    yield _sse(evento)

            return StreamingResponse(
                cuerpo(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            )

        return await atender(request, accion)

    @router.patch("/conversaciones/{id_}/mensajes/{mensaje_id}")
    async def marcar(id_: UUID, mensaje_id: UUID, request: Request):
        async def accion(a):
            datos = await entrada(request, Conservar)
            return {"mensaje": _json(servicio.marcar(a, id_, mensaje_id, datos.conservar_en_insumo))}

        return await atender(request, accion)

    @router.post("/conversaciones/{id_}/insumo")
    async def exportar(id_: UUID, request: Request):
        async def accion(a):
            datos = await entrada(request, ExportarInsumo)
            insumo = servicio.exportar(a, id_, datos.objetivo, datos.restricciones, datos.preguntas_abiertas)
            return JSONResponse({"insumo": _json(insumo)}, status_code=201)

        return await atender(request, accion)

    return router
