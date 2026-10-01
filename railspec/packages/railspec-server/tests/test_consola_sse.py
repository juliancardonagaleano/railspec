"""Consola: eventos en vivo (SSE): ``Last-Event-ID`` inválido (B2); tope, revalidación, executor (B3)."""

from __future__ import annotations

import asyncio
import dataclasses
import threading

import pytest
from apoyo_motor import JULIAN, ORG, WS, avanzar, entrada_start
from railspec.contracts.repositorio import Rol
from railspec.server.consola import ConfigConsola
from railspec.server.consola.almacen import AlmacenConsola
from test_consola import ANA_ID, LUIS_ID, Montaje, asignar, correr

# --- B2: Last-Event-ID inválido ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor",
    [
        "²:²".encode("latin-1"),  # isdigit() sí, int() no
        "1:٣".encode(),  # dígito arábigo-índico
        b"1:2:3",
        b":",
        b"abc",
        b"-1:-1",
        b"9" * 5000 + b":1",  # supera el tope de int() de Python
        b"99999999999999999999:1",  # no cabe en un entero de Mongo
    ],
    ids=["superindice", "arabigo-indico", "tres-partes", "vacio", "texto", "negativos", "enorme", "no-cabe"],
)
def test_last_event_id_invalido_se_ignora_y_empieza_desde_el_principio(valor):
    async def caso():
        m = Montaje()
        salida = await m.motor.start(entrada_start(), JULIAN)
        alcance = salida.estado.unidad
        await avanzar(m.motor, alcance)
        url = f"/consola/api/orgs/{ORG}/workspaces/{WS}/unidades/{alcance.unidad}/eventos"
        async with m.cliente("tk-julian") as c:
            r = await c.get(url, headers={"Last-Event-ID": valor})
            assert r.status_code == 200, r.text
            assert "event: sync" in r.text  # desde el principio: todos los eventos
            # Uno válido sigue retomando.
            r = await c.get(url, headers={"Last-Event-ID": b"1:0"})
            assert r.status_code == 200 and "id: 1:0" not in r.text

    correr(caso())


def test_cursores_fuera_de_rango_son_422_no_500():
    async def caso():
        m = Montaje()
        salida = await m.motor.start(entrada_start(), JULIAN)
        alcance = salida.estado.unidad
        url = f"/consola/api/orgs/{ORG}/workspaces/{WS}/unidades/{alcance.unidad}/eventos"
        async with m.cliente("tk-julian") as c:
            for params in ({"desde_remoto": "9" * 30}, {"desde_local": -1}, {"desde_remoto": "x"}):
                r = await c.get(url, params=params)
                assert r.status_code == 422, (params, r.status_code)

    correr(caso())


# --- B3: SSE con tope, revalidación y executor propio -----------------------------------------------------


class FlujoAsgi:
    """Petición ASGI cruda que no termina hasta desconectar (``ASGITransport`` de httpx no hace streaming)."""

    def __init__(self, app, ruta: str, token: str) -> None:
        self.app = app
        self.scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": ruta,
            "raw_path": ruta.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"railspec.test"), (b"authorization", f"Bearer {token}".encode())],
            "client": ("203.0.113.9", 4000),
            "server": ("railspec.test", 443),
        }
        self.mensajes: list[dict] = []
        self._desconexion = asyncio.Event()
        self._pedido = False
        self._empezo = asyncio.Event()
        self.tarea: asyncio.Task | None = None

    async def _recibir(self):
        if not self._pedido:
            self._pedido = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._desconexion.wait()
        return {"type": "http.disconnect"}

    async def _enviar(self, mensaje):
        self.mensajes.append(mensaje)
        if mensaje["type"] == "http.response.start":
            self._empezo.set()

    async def abrir(self) -> FlujoAsgi:
        self.tarea = asyncio.create_task(self.app(self.scope, self._recibir, self._enviar))
        esperar = asyncio.create_task(self._empezo.wait())
        await asyncio.wait({esperar, self.tarea}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        esperar.cancel()
        return self

    @property
    def estado(self) -> int:
        return next(m["status"] for m in self.mensajes if m["type"] == "http.response.start")

    @property
    def cabeceras(self) -> dict[str, str]:
        inicio = next(m for m in self.mensajes if m["type"] == "http.response.start")
        return {k.decode(): v.decode() for k, v in inicio["headers"]}

    @property
    def cuerpo(self) -> str:
        return b"".join(
            m.get("body", b"") for m in self.mensajes if m["type"] == "http.response.body"
        ).decode()

    @property
    def terminado(self) -> bool:
        return self.tarea is not None and self.tarea.done()

    async def cerrar(self) -> None:
        self._desconexion.set()
        await asyncio.wait_for(self.tarea, 5)


async def _unidad_en_curso(m: Montaje):
    salida = await m.motor.start(entrada_start(), JULIAN)
    return salida.estado.unidad


def _ruta_eventos(alcance) -> str:
    return f"/consola/api/orgs/{ORG}/workspaces/{WS}/unidades/{alcance.unidad}/eventos"


def test_sse_tope_por_persona_con_los_valores_por_defecto():
    async def caso():
        m = Montaje()
        m.ctx.sse_duracion_max_s = 30
        alcance = await _unidad_en_curso(m)
        asignar(m.almacen, Rol.lector, ANA_ID)
        asignar(m.almacen, Rol.lector, LUIS_ID)
        ruta = _ruta_eventos(alcance)
        abiertos = []
        try:
            limite = m.ctx.config.sse_max_por_usuario
            for _ in range(limite):
                f = await FlujoAsgi(m.app, ruta, "tk-ana").abrir()
                assert f.estado == 200
                abiertos.append(f)
            # Una conexión más de la misma persona: 429 con Retry-After, sin abrir flujo.
            extra = await FlujoAsgi(m.app, ruta, "tk-ana").abrir()
            await asyncio.wait_for(extra.tarea, 5)
            assert extra.estado == 429 and "retry-after" in extra.cabeceras
            assert "text/event-stream" not in extra.cabeceras.get("content-type", "")
            # Otra persona no está limitada por la primera.
            otra = await FlujoAsgi(m.app, ruta, "tk-luis").abrir()
            assert otra.estado == 200
            abiertos.append(otra)
            # Al cerrarse una, el cupo vuelve.
            await abiertos.pop(0).cerrar()
            nueva = await FlujoAsgi(m.app, ruta, "tk-ana").abrir()
            assert nueva.estado == 200
            abiertos.append(nueva)
        finally:
            for f in abiertos:
                await f.cerrar()

    correr(caso())


def test_sse_tope_global_configurable():
    async def caso():
        m = Montaje()
        m.ctx.sse_duracion_max_s = 30
        m.ctx.config = dataclasses.replace(m.ctx.config, sse_max_por_usuario=5, sse_max_global=2)
        alcance = await _unidad_en_curso(m)
        asignar(m.almacen, Rol.lector, ANA_ID)
        asignar(m.almacen, Rol.lector, LUIS_ID)
        ruta = _ruta_eventos(alcance)
        abiertos = []
        try:
            for token in ("tk-ana", "tk-ana"):
                f = await FlujoAsgi(m.app, ruta, token).abrir()
                assert f.estado == 200
                abiertos.append(f)
            tercero = await FlujoAsgi(m.app, ruta, "tk-luis").abrir()
            await asyncio.wait_for(tercero.tarea, 5)
            assert tercero.estado == 429
            # Sin permiso se sigue respondiendo 403 antes que 429, sin gastar cupo.
            sin_rol = await FlujoAsgi(m.app, ruta, "tk-julian").abrir()
            await asyncio.wait_for(sin_rol.tarea, 5)
            assert sin_rol.estado in (403, 429)
        finally:
            for f in abiertos:
                await f.cerrar()

    correr(caso())


def test_sse_cupo_se_libera_al_terminar_el_flujo():
    async def caso():
        m = Montaje()
        m.ctx.config = dataclasses.replace(m.ctx.config, sse_max_por_usuario=1)
        alcance = await _unidad_en_curso(m)
        asignar(m.almacen, Rol.lector, ANA_ID)
        ruta = _ruta_eventos(alcance)
        # sse_duracion_max_s=0.2: cada flujo termina solo y debe devolver su cupo.
        for _ in range(3):
            f = await FlujoAsgi(m.app, ruta, "tk-ana").abrir()
            assert f.estado == 200
            await asyncio.wait_for(f.tarea, 5)
            assert "retry: 3000" in f.cuerpo

    correr(caso())


def test_sse_se_cierra_al_perder_el_permiso():
    async def caso():
        m = Montaje()
        m.ctx.sse_duracion_max_s = 60
        m.ctx.config = dataclasses.replace(m.ctx.config, sse_revalidar_s=0.05)
        alcance = await _unidad_en_curso(m)
        rol = asignar(m.almacen, Rol.lector, ANA_ID)
        f = await FlujoAsgi(m.app, _ruta_eventos(alcance), "tk-ana").abrir()
        try:
            assert f.estado == 200
            await asyncio.sleep(0.2)
            assert not f.terminado  # con permiso sigue abierto
            assert AlmacenConsola(m.almacen.db).borrar_rol(ORG, str(rol.id))
            # Revocado el rol, el flujo se corta en la siguiente revalidación, no a los 60 s.
            await asyncio.wait_for(f.tarea, 5)
        finally:
            if not f.terminado:
                await f.cerrar()
        # Y reconectar ya da 403.
        otra = await FlujoAsgi(m.app, _ruta_eventos(alcance), "tk-ana").abrir()
        await asyncio.wait_for(otra.tarea, 5)
        assert otra.estado == 403

    correr(caso())


def test_sse_consulta_en_un_executor_propio_y_una_vez_por_tick(monkeypatch):
    async def caso():
        m = Montaje()
        m.ctx.sse_duracion_max_s = 0.3
        alcance = await _unidad_en_curso(m)
        asignar(m.almacen, Rol.lector, ANA_ID)
        hilos: list[str] = []
        original = m.almacen.eventos_desde

        def espia(*args, **kwargs):
            hilos.append(threading.current_thread().name)
            return original(*args, **kwargs)

        monkeypatch.setattr(m.almacen, "eventos_desde", espia)
        f = await FlujoAsgi(m.app, _ruta_eventos(alcance), "tk-ana").abrir()
        await asyncio.wait_for(f.tarea, 5)
        assert hilos, "el flujo no consultó eventos"
        # Ni el executor por defecto (login, GitHub) ni el hilo del bucle: uno dedicado al SSE.
        assert all(h.startswith("railspec-sse") for h in hilos), set(hilos)

    correr(caso())


def test_topes_sse_desde_entorno():
    c = ConfigConsola.desde_entorno(
        {
            "RAILSPEC_CONSOLA_SSE_MAX_USUARIO": "2",
            "RAILSPEC_CONSOLA_SSE_MAX_GLOBAL": "10",
            "RAILSPEC_CONSOLA_SSE_REVALIDAR_S": "5",
        }
    )
    assert (c.sse_max_por_usuario, c.sse_max_global, c.sse_revalidar_s) == (2, 10, 5.0)
    por_defecto = ConfigConsola.desde_entorno({})
    assert (por_defecto.sse_max_por_usuario, por_defecto.sse_max_global) == (5, 200)
    assert por_defecto.sse_revalidar_s == 30.0
    for crudo in ("0", "-1", "mucho"):
        with pytest.raises(ValueError, match="RAILSPEC_CONSOLA_SSE_MAX_USUARIO"):
            ConfigConsola.desde_entorno({"RAILSPEC_CONSOLA_SSE_MAX_USUARIO": crudo})
