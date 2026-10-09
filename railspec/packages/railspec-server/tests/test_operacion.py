"""Operación del servidor: versión del esquema del estado, ``GET /metrics`` (con sus claves por origen),
la pantalla de operación de la consola y la redirección de ``/``."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from railspec.server.app import ensamblar
from railspec.server.claves_metricas import PREFIJO, ClavesMetricasMongo, ErrorClaveMetricas
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
        # «julian» administra la plataforma; «ana» no.
        tokens_desarrollo={"dev": ("julian", 1), "ana": ("ana", 2)},
        metricas_token=token,
        consola=ConfigConsola(carpeta_spa=str(tmp_path) if spa else None, administradores=frozenset({1})),
    )
    return ensamblar(config)[1]


ADMIN = {"Authorization": "Bearer dev", "x-railspec-consola": "1"}
OTRA = {"Authorization": "Bearer ana", "x-railspec-consola": "1"}


def _bearer(clave: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {clave}"}


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


# --- claves de métricas por origen ----------------------------------------------------------------------


def test_una_clave_creada_en_la_consola_abre_metrics_hasta_que_se_revoca(tmp_path):
    async def caso():
        async with _cliente(_app(tmp_path, token=None)) as c:
            assert (await c.get("/metrics")).status_code == 404  # sin token ni claves: no existe
            r = await c.post("/consola/api/metricas/claves", json={"nombre": "Grafana"}, headers=ADMIN)
            assert r.status_code == 201
            creada = r.json()
            secreto = creada["secreto"]
            assert secreto.startswith(PREFIJO) and len(secreto) > 40
            assert creada["clave"]["prefijo"] == secreto[: len(creada["clave"]["prefijo"])]
            assert (await c.get("/metrics")).status_code == 401  # ya existe, pero exige clave
            assert (await c.get("/metrics", headers=_bearer(PREFIJO + "otra"))).status_code == 401
            ok = await c.get("/metrics", headers=_bearer(secreto))
            assert ok.status_code == 200 and "railspec_info" in ok.text

            lista = (await c.get("/consola/api/metricas/claves", headers=ADMIN)).json()
            assert [(k["nombre"], k["activa"], k["creada_por"]) for k in lista] == [
                ("Grafana", True, "julian")
            ]
            assert lista[0]["ultimo_uso"] is not None
            assert secreto not in str(lista) and "hash" not in lista[0] and "_id" not in lista[0]

            r = await c.delete(f"/consola/api/metricas/claves/{lista[0]['id']}", headers=ADMIN)
            assert r.status_code == 200
            assert r.json()["activa"] is False and r.json()["revocada_por"] == "julian"
            # Sin claves activas ni token vuelve a no existir; con otra activa, la revocada da 401.
            assert (await c.get("/metrics", headers=_bearer(secreto))).status_code == 404
            await c.post("/consola/api/metricas/claves", json={"nombre": "otra"}, headers=ADMIN)
            assert (await c.get("/metrics", headers=_bearer(secreto))).status_code == 401
            ajena = await c.delete("/consola/api/metricas/claves/no-existe", headers=ADMIN)
            assert ajena.status_code == 404

    asyncio.run(caso())


def test_el_token_del_entorno_y_las_claves_valen_a_la_vez(tmp_path):
    async def caso():
        async with _cliente(_app(tmp_path)) as c:
            r = await c.post("/consola/api/metricas/claves", json={"nombre": "script"}, headers=ADMIN)
            secreto = r.json()["secreto"]
            assert (await c.get("/metrics", headers=_bearer(TOKEN))).status_code == 200
            assert (await c.get("/metrics", headers=_bearer(secreto))).status_code == 200

    asyncio.run(caso())


def test_nombres_de_clave_repetidos_vacios_o_largos_se_rechazan(tmp_path):
    async def caso():
        async with _cliente(_app(tmp_path)) as c:
            ruta = "/consola/api/metricas/claves"
            assert (await c.post(ruta, json={"nombre": "Grafana"}, headers=ADMIN)).status_code == 201
            repetida = await c.post(ruta, json={"nombre": " grafana "}, headers=ADMIN)
            assert repetida.status_code == 409 and "ya hay una clave activa" in repetida.text
            assert (await c.post(ruta, json={"nombre": "   "}, headers=ADMIN)).status_code == 422
            assert (await c.post(ruta, json={"nombre": "x" * 61}, headers=ADMIN)).status_code == 422
            assert (await c.post(ruta, json={"nombre": "y", "extra": 1}, headers=ADMIN)).status_code == 422

    asyncio.run(caso())


def test_solo_quien_administra_la_plataforma_ve_la_operacion_y_las_claves(tmp_path):
    async def caso():
        async with _cliente(_app(tmp_path)) as c:
            for metodo, ruta, cuerpo in (
                ("GET", "/consola/api/operacion", None),
                ("GET", "/consola/api/metricas/claves", None),
                ("POST", "/consola/api/metricas/claves", {"nombre": "x"}),
                ("DELETE", "/consola/api/metricas/claves/x", None),
            ):
                r = await c.request(metodo, ruta, json=cuerpo, headers=OTRA)
                assert r.status_code == 403, (metodo, ruta)
                assert (await c.request(metodo, ruta, json=cuerpo)).status_code == 401, (metodo, ruta)

    asyncio.run(caso())


def test_la_consola_muestra_la_operacion_sin_clave_y_sin_el_token(tmp_path):
    async def caso():
        async with _cliente(_app(tmp_path)) as c:
            await c.get("/healthz")
            await c.get("/otra-cosa")
            r = await c.get("/consola/api/operacion", headers=ADMIN)
        assert r.status_code == 200
        v = r.json()
        assert v["version"] == "0.1.0" and v["token_entorno"] is True
        assert v["esquema"] == {"codigo": VERSION_ESQUEMA, "almacenado": VERSION_ESQUEMA}
        assert v["sondas"] == []  # en memoria no hay bases que sondear
        assert {"grupo": "sondas", "metodo": "GET", "estado": "2xx", "total": 1} in v["peticiones"]
        assert {"grupo": "otras", "metodo": "GET", "estado": "4xx", "total": 1} in v["peticiones"]
        assert datetime.fromisoformat(v["inicio"]) <= datetime.fromisoformat(v["ahora"])
        assert TOKEN not in r.text

    asyncio.run(caso())


def test_el_ultimo_uso_se_anota_como_mucho_una_vez_por_minuto():
    claves = ClavesMetricasMongo(almacen_en_memoria().db)
    t0 = datetime(2026, 10, 9, 12, tzinfo=UTC)
    clave, secreto = claves.crear("Grafana", "julian", t0)
    assert claves.validar(secreto, t0 + timedelta(seconds=5)).ultimo_uso == t0 + timedelta(seconds=5)
    assert claves.validar(secreto, t0 + timedelta(seconds=30)).ultimo_uso == t0 + timedelta(seconds=5)
    assert claves.validar(secreto, t0 + timedelta(minutes=2)).ultimo_uso == t0 + timedelta(minutes=2)
    assert claves.validar("sin-prefijo", t0) is None
    assert claves.revocar(clave.id, "julian", t0 + timedelta(minutes=3)).revocada_en is not None
    # Revocar dos veces no mueve la fecha ni quién lo hizo.
    otra_vez = claves.revocar(clave.id, "ana", t0 + timedelta(minutes=9))
    assert (otra_vez.revocada_en, otra_vez.revocada_por) == (t0 + timedelta(minutes=3), "julian")
    assert claves.validar(secreto, t0 + timedelta(minutes=4)) is None and not claves.hay_activas()
    # Revocada, el nombre queda libre.
    assert claves.crear("Grafana", "julian", t0 + timedelta(minutes=5))[0].nombre == "Grafana"


def test_hay_un_tope_de_claves_activas():
    from railspec.server.claves_metricas import MAX_ACTIVAS

    claves = ClavesMetricasMongo(almacen_en_memoria().db)
    ahora = datetime(2026, 10, 9, tzinfo=UTC)
    for i in range(MAX_ACTIVAS):
        claves.crear(f"origen-{i}", "julian", ahora)
    with pytest.raises(ErrorClaveMetricas, match="revoca las que no uses"):
        claves.crear("una-mas", "julian", ahora)


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
