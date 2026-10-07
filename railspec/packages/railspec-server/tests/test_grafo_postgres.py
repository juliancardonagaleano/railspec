"""El grafo central en la base Postgres del estado (``RAILSPEC_GRAFO_POSTGRES``): configuración y servidor."""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid

import httpx
import pytest
from apoyo_motor import vinculo
from railspec.contracts.comun import NivelCodigo
from railspec.server.app import _sondas, ensamblar
from railspec.server.config import Configuracion, ErrorConfiguracion, validar_arranque
from test_oidc_index import ALCANCE, COMMIT_1, REPO_GH, _oidc, lote, simbolo, verificador

URL = os.environ.get("RAILSPEC_PRUEBAS_POSTGRES")
DESARROLLO = {
    "RAILSPEC_TOKENS_DESARROLLO": "tk-julian=juliancardonagaleano:1",
    "RAILSPEC_PERMITIR_DESARROLLO": "1",
}


def test_la_bandera_se_lee_y_por_defecto_no_hay_grafo_en_postgres():
    assert Configuracion.desde_entorno({}).grafo_postgres is False
    c = Configuracion.desde_entorno(
        {"RAILSPEC_POSTGRES_URL": "postgresql://x", "RAILSPEC_GRAFO_POSTGRES": "true"}
    )
    assert c.grafo_postgres is True


def test_el_grafo_en_postgres_exige_la_base_del_estado_y_excluye_a_falkordb():
    sin_base = Configuracion.desde_entorno({**DESARROLLO, "RAILSPEC_GRAFO_POSTGRES": "1"})
    with pytest.raises(ErrorConfiguracion, match="RAILSPEC_POSTGRES_URL"):
        validar_arranque(sin_base)
    doble = Configuracion.desde_entorno(
        {
            "RAILSPEC_POSTGRES_URL": "postgresql://x",
            "RAILSPEC_GRAFO_POSTGRES": "1",
            "RAILSPEC_FALKORDB_URL": "redis://falkor:6379",
        }
    )
    with pytest.raises(ErrorConfiguracion, match="un solo motor"):
        validar_arranque(doble)


def test_sin_la_bandera_un_postgres_de_estado_no_crea_grafo():
    config = Configuracion.desde_entorno({**DESARROLLO, "RAILSPEC_POSTGRES_URL": "postgresql://x"})
    assert set(_sondas(config, object(), None)) == {"postgres"}


@pytest.mark.skipif(not URL, reason="sin RAILSPEC_PRUEBAS_POSTGRES")
def test_graph_index_y_graph_query_sobre_el_motor_de_postgres():
    esquema = "t_" + uuid.uuid4().hex[:12]
    config = Configuracion.desde_entorno(
        {
            **DESARROLLO,
            "RAILSPEC_POSTGRES_URL": URL,
            "RAILSPEC_POSTGRES_ESQUEMA": esquema,
            "RAILSPEC_GRAFO_POSTGRES": "1",
        }
    )

    @contextlib.asynccontextmanager
    async def servidor():
        motor, app = ensamblar(config, verificador_oidc=verificador(repositorios=frozenset({REPO_GH})))
        motor.n.almacen.guardar_configuracion([vinculo(NivelCodigo.restringido)])
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://railspec"
                ) as c:
                    yield c, motor
        finally:
            import psycopg

            with psycopg.connect(URL, autocommit=True) as conn:
                conn.execute(f'DROP SCHEMA IF EXISTS "{esquema}" CASCADE')

    async def caso():
        async with servidor() as (c, motor):
            assert {"graph.index", "graph.query"} <= {
                t["name"] for t in (await c.get("/v1/tools")).json()["tools"]
            }
            assert (await c.get("/healthz")).json()["grafo"] == "ok"

            r = await c.post("/v1/tools/graph.index", json=lote(1, 1, simbolo("firmar")), headers=_oidc())
            assert r.status_code == 200 and r.json()["aplicado"] is True, r.text
            acceso = motor.n.grafo._acceso
            assert type(acceso._motor).__name__ == "MotorPostgres"
            assert acceso.espacio(ALCANCE).meta().commit == COMMIT_1

    asyncio.run(caso())
