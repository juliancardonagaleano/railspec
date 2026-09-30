"""Fixtures: cada prueba corre contra el doble en memoria y, si hay
``RAILSPEC_FALKORDB_URL``, contra FalkorDB real (p. ej. ``docker run -p 6379:6379
falkordb/falkordb``). En FalkorDB cada prueba usa una organización propia y la
borra al terminar."""

from __future__ import annotations

import os
import uuid

import pytest
from railspec.graph.memoria import MotorMemoria

_URL = os.environ.get("RAILSPEC_FALKORDB_URL")


def _motor_falkor():
    from railspec.graph.motor_falkordb import MotorFalkor

    return MotorFalkor.desde_url(_URL)


PARAMS = ["memoria"] + (["falkordb"] if _URL else [])


@pytest.fixture(params=PARAMS)
def motor(request):
    if request.param == "memoria":
        yield MotorMemoria()
        return
    m = _motor_falkor()
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
