"""Fixtures: cada prueba corre contra el doble en memoria y, si hay
``RAILSPEC_FALKORDB_URL``, contra FalkorDB real (p. ej. ``docker run -p 6379:6379
falkordb/falkordb``) y, si hay ``RAILSPEC_PRUEBAS_POSTGRES``, contra Postgres (el mismo nombre
que usa la suite del servidor). En los motores reales cada prueba usa una organización propia y
la borra al terminar."""

from __future__ import annotations

import os
import uuid

import pytest
from railspec.graph.memoria import MotorMemoria

_URL = os.environ.get("RAILSPEC_FALKORDB_URL")


def _motor_falkor():
    from railspec.graph.motor_falkordb import MotorFalkor

    return MotorFalkor.desde_url(_URL)


_URL_PG = os.environ.get("RAILSPEC_PRUEBAS_POSTGRES")

PARAMS = ["memoria"] + (["falkordb"] if _URL else []) + (["postgres"] if _URL_PG else [])

_pg = None


def _motor_postgres():
    """Un solo pool para toda la sesión: uno por prueba agotaría las conexiones de Postgres."""

    global _pg
    if _pg is None:
        from railspec.graph.motor_postgres import MotorPostgres

        _pg = MotorPostgres.desde_url(_URL_PG, esquema="railspec_pruebas_grafo")
    return _pg


@pytest.fixture(params=PARAMS)
def motor(request):
    if request.param == "memoria":
        yield MotorMemoria()
        return
    m = _motor_postgres() if request.param == "postgres" else _motor_falkor()
    yield m
    org = request.node.stash.get(ORG_KEY, None)
    if org:
        for g in m.listar(f"railspec:{org}:"):
            m.borrar(g)


ORG_KEY = pytest.StashKey[str]()


@pytest.fixture
def org(request) -> str:
    """Organización única por prueba para aislar corridas sobre el mismo FalkorDB."""

    valor = "t" + uuid.uuid4().hex[:10]
    request.node.stash[ORG_KEY] = valor
    return valor


@pytest.fixture(scope="session", autouse=True)
def _cerrar_pool_postgres():
    yield
    if _pg is not None:
        _pg.cerrar()
