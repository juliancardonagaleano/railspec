"""Operación del servidor: versión del esquema del estado, ``GET /metrics`` y la redirección de ``/``."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from railspec.server.app import ensamblar
from railspec.server.config import Configuracion
from railspec.server.consola.config import ConfigConsola
from railspec.server.estado import (
    VERSION_ESQUEMA,
    EsquemaIncompatible,
    almacen_en_memoria,
    asegurar_esquema,
)
from railspec.server.estado.esquema import COLECCION, ID_DOCUMENTO

# --- versión del esquema ------------------------------------------------------------------------------


def _guardada(db):
    return db[COLECCION].find_one({"_id": ID_DOCUMENTO})["version"]


def test_base_vacia_queda_en_la_version_del_codigo():
    db = almacen_en_memoria().db
    v = asegurar_esquema(db)
    assert (v.codigo, v.almacenada) == (VERSION_ESQUEMA, VERSION_ESQUEMA)
    assert _guardada(db) == VERSION_ESQUEMA
    assert asegurar_esquema(db) == v  # reiniciar no cambia nada


def test_un_estado_mas_nuevo_que_el_codigo_no_arranca():
    db = almacen_en_memoria().db
    asegurar_esquema(db, version=3, migraciones={1: lambda _: None, 2: lambda _: None})
    with pytest.raises(EsquemaIncompatible, match="esquema 3.*hasta el 2"):
        asegurar_esquema(db, version=2)
    assert _guardada(db) == 3  # el servidor viejo no toca lo que no entiende


def test_migra_en_orden_y_solo_una_vez():
    db = almacen_en_memoria().db
    asegurar_esquema(db, version=1)
    pasos: list[int] = []
    migraciones = {1: lambda _: pasos.append(1), 2: lambda _: pasos.append(2)}
    v = asegurar_esquema(db, version=3, migraciones=migraciones)
    assert pasos == [1, 2] and (v.codigo, v.almacenada) == (3, 3) and _guardada(db) == 3
    asegurar_esquema(db, version=3, migraciones=migraciones)
    assert pasos == [1, 2]


def test_falta_una_migracion():
    db = almacen_en_memoria().db
    asegurar_esquema(db, version=1)
    with pytest.raises(EsquemaIncompatible, match="no hay migración al 2"):
        asegurar_esquema(db, version=2, migraciones={})
    assert _guardada(db) == 1


def test_una_migracion_que_falla_deja_la_version_anterior():
    db = almacen_en_memoria().db
    asegurar_esquema(db, version=1)

    def rota(_):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        asegurar_esquema(db, version=2, migraciones={1: rota})
    assert _guardada(db) == 1


# --- configuración ----------------------------------------------------------------------------------


def test_token_de_metricas_desde_entorno():
    assert Configuracion.desde_entorno({}).metricas_token is None
    assert Configuracion.desde_entorno({"RAILSPEC_METRICAS_TOKEN": "  "}).metricas_token is None
    assert Configuracion.desde_entorno({"RAILSPEC_METRICAS_TOKEN": "x" * 16}).metricas_token == "x" * 16
    with pytest.raises(ValueError, match="adivinable"):
        Configuracion.desde_entorno({"RAILSPEC_METRICAS_TOKEN": "corto"})


# --- raíz y métricas sobre la app ensamblada --------------------------------------------------------------

TOKEN = "t" * 24


def _app(tmp_path, *, spa=True, token=TOKEN):
    (tmp_path / "index.html").write_text("<!doctype html><title>Railspec</title>")
    config = Configuracion(
        permitir_desarrollo=True,
        tokens_desarrollo={"dev": ("julian", 1)},
        metricas_token=token,
        consola=ConfigConsola(carpeta_spa=str(tmp_path) if spa else None),
    )
    return ensamblar(config)[1]


def _cliente(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://railspec.test")


def test_la_raiz_redirige_a_la_consola(tmp_path):
    async def caso():
        async with _cliente(_app(tmp_path)) as c:
            r = await c.get("/")
            assert r.status_code == 307 and r.headers["location"] == "/consola/"
            assert "Railspec" in (await c.get("/consola/")).text

    asyncio.run(caso())


def test_sin_spa_la_raiz_sigue_sin_redirigir(tmp_path):
    async def caso():
        async with _cliente(_app(tmp_path, spa=False)) as c:
            assert (await c.get("/")).status_code == 404

    asyncio.run(caso())


def test_metricas_exigen_el_token(tmp_path):
    async def caso():
        async with _cliente(_app(tmp_path)) as c:
            assert (await c.get("/metrics")).status_code == 401
            mal = await c.get("/metrics", headers={"Authorization": "Bearer " + "x" * 24})
            assert mal.status_code == 401 and mal.headers["www-authenticate"] == "Bearer"

    asyncio.run(caso())


def test_metricas_sin_token_configurado_no_existen(tmp_path):
    async def caso():
        async with _cliente(_app(tmp_path, token=None)) as c:
            assert (await c.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 404

    asyncio.run(caso())


def test_metricas_publican_version_de_esquema_sondas_y_peticiones(tmp_path):
    async def caso():
        async with _cliente(_app(tmp_path)) as c:
            await c.get("/healthz")
            await c.get("/v1/tools")
            await c.get("/consola/api/no-existe")
            await c.get("/otra-cosa")
            r = await c.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain; version=0.0.4")
        texto = r.text
        assert 'railspec_info{version="0.1.0"} 1' in texto
        assert f'railspec_estado_esquema{{origen="codigo"}} {VERSION_ESQUEMA}' in texto
        assert f'railspec_estado_esquema{{origen="almacenado"}} {VERSION_ESQUEMA}' in texto
        assert 'railspec_http_peticiones_total{grupo="sondas",metodo="GET",estado="2xx"} 1' in texto
        assert 'railspec_http_peticiones_total{grupo="v1",metodo="GET",estado="2xx"} 1' in texto
        assert 'railspec_http_peticiones_total{grupo="consola_api",metodo="GET",estado="4xx"} 1' in texto
        assert 'railspec_http_peticiones_total{grupo="otras",metodo="GET",estado="4xx"} 1' in texto
        assert "railspec_proceso_inicio_segundos " in texto
        assert TOKEN not in texto

    asyncio.run(caso())


def test_sonda_caida_se_ve_en_las_metricas():
    async def caso():
        from railspec.server.estado import VersionEsquema
        from railspec.server.metricas import Metricas, renderizar

        def cae():
            raise RuntimeError("sin base")

        texto = await renderizar(
            Metricas(),
            version_app="1",
            esquema=VersionEsquema(1, 1),
            sondas={"postgres": lambda: None, "falkordb": cae},
        )
        assert 'railspec_sonda_ok{sonda="postgres"} 1' in texto
        assert 'railspec_sonda_ok{sonda="falkordb"} 0' in texto

    asyncio.run(caso())


def test_el_arranque_aborta_si_el_estado_es_de_un_esquema_mas_nuevo(monkeypatch):
    import railspec.server.app as app_mod

    db = almacen_en_memoria()
    asegurar_esquema(db.db, version=VERSION_ESQUEMA + 1, migraciones={VERSION_ESQUEMA: lambda _: None})
    monkeypatch.setattr(app_mod, "almacen_en_memoria", lambda: db)
    with pytest.raises(EsquemaIncompatible):
        ensamblar(Configuracion(permitir_desarrollo=True))
