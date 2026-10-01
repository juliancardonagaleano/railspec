"""Identidad bajo carga (M5): resolver un token no puede bloquear el event loop ni crecer sin tope.

Antes, ``POST /v1/tools/x`` con ``Bearer <aleatorio>`` llamaba a GitHub con un ``httpx.Client`` síncrono
dentro del event loop: unas decenas por segundo bastaban para que /livez no respondiera y el kubelet
reiniciara las réplicas.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time

import httpx
from fastapi import FastAPI
from railspec.contracts.comun import Actor, Canal, TipoActor
from railspec.server.api.identidad import TokenInvalido
from railspec.server.api.registro import Registro
from railspec.server.api.superficies import _actor, aplicacion
from railspec.server.chat.http import router_chat

ESPERA_S = 0.5
BASURA = 8
#: Con el loop libre la parada es de milisegundos; bloqueado, cada token basura lo frena ``ESPERA_S``.
TOPE_PARADA_S = 0.25


class IdentidadLenta:
    """Como ``IdentidadGithub`` ante GitHub lento: bloquea el hilo que la llama y rechaza el token."""

    nombre = "lenta"

    def __init__(self) -> None:
        self.hilos: set[int] = set()

    def actor_desde_token(self, token: str, canal: str) -> Actor:
        self.hilos.add(threading.get_ident())
        time.sleep(ESPERA_S)
        raise TokenInvalido("GitHub rechazó el token (401)")


@contextlib.asynccontextmanager
async def cliente(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
            yield c


async def _mayor_parada_del_loop(c: httpx.AsyncClient, peticiones) -> float:
    """Lanza ``peticiones`` y devuelve la mayor parada del event loop mientras se atienden.

    Un latido pide dormir 10 ms; si algo bloquea el loop, el latido llega tarde y el kubelet vería
    /livez colgado. Con el loop libre la parada es de milisegundos."""

    parada = 0.0
    activo = True

    async def latido() -> None:
        nonlocal parada
        while activo:
            t0 = time.monotonic()
            await asyncio.sleep(0.01)
            parada = max(parada, time.monotonic() - t0 - 0.01)

    pulso = asyncio.create_task(latido())
    tareas = [asyncio.create_task(p) for p in peticiones]
    respuestas = await asyncio.gather(*tareas)
    activo = False
    await pulso
    assert all(r.status_code == 401 for r in respuestas), [r.text for r in respuestas]
    r = await c.get("/livez")
    assert r.status_code == 200
    return parada


def test_tokens_basura_no_bloquean_el_loop_en_http_y_mcp():
    async def caso():
        identidad = IdentidadLenta()
        app = aplicacion(Registro({}, None), identidad)
        async with cliente(app) as c:
            basura = [
                c.post("/v1/tools/unit.list", json={}, headers={"Authorization": f"Bearer basura-{i}"})
                for i in range(BASURA)
            ]
            assert await _mayor_parada_del_loop(c, basura) < TOPE_PARADA_S
        # Y la identidad se resolvió fuera del hilo del loop.
        assert identidad.hilos and threading.get_ident() not in identidad.hilos

    asyncio.run(caso())


def test_tokens_basura_no_bloquean_el_loop_en_el_chat():
    async def caso():
        app = FastAPI()
        app.include_router(router_chat(None, IdentidadLenta()))

        @app.get("/livez")
        async def livez() -> dict[str, str]:
            return {"estado": "ok"}

        async with cliente(app) as c:
            basura = [
                c.post(
                    "/v1/chat/conversaciones",
                    json={"alcance": {"org": "acme", "workspace": "pagos"}},
                    headers={"Authorization": f"Bearer basura-{i}"},
                )
                for i in range(BASURA)
            ]
            assert await _mayor_parada_del_loop(c, basura) < TOPE_PARADA_S

    asyncio.run(caso())


def test_actor_resuelve_la_identidad_en_otro_hilo():
    visto: list[int] = []
    esperado = Actor(tipo=TipoActor.humano, canal=Canal.arnes, github_id=7, login="ana")

    class Identidad:
        def actor_desde_token(self, token: str, canal: str) -> Actor:
            visto.append(threading.get_ident())
            return esperado

    async def caso():
        assert await _actor(Identidad(), "Bearer t", "arnes") == esperado
        try:
            await _actor(Identidad(), None, "arnes")
        except TokenInvalido as exc:
            assert "Authorization" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("sin cabecera debe rechazar")

    asyncio.run(caso())
    assert visto and threading.get_ident() not in visto
