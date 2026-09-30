"""sync.pull y sync.push (contrato 1.3)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from apoyo_motor import JULIAN, REPO, avanzar, construir, entrada_start, reporte
from railspec.contracts.eventos import CommitEmpujado, Direccion
from railspec.contracts.tools import (
    CodigoError,
    EventoSubida,
    Superficie,
    SyncPullEntrada,
    SyncPushEntrada,
)
from railspec.server.api import AutorizadorRoles, Registro
from railspec.server.motor import ErrorNegocio

AHORA = datetime(2026, 9, 30, 21, tzinfo=UTC)


def subida(secuencia: int, id_: uuid.UUID | None = None) -> EventoSubida:
    return EventoSubida(
        id=id_ or uuid.uuid4(),
        secuencia=secuencia,
        emitido_en=AHORA,
        carga=CommitEmpujado(repositorio=REPO, rama="railspec/0001", commit=f"{secuencia:040x}"),
    )


def test_pull_pagina_los_eventos_remoto_a_local():
    async def caso():
        motor, _ = construir()
        alcance = (await motor.start(entrada_start(), JULIAN)).estado.unidad
        av = await avanzar(motor, alcance)
        await motor.report(reporte(av.orden), JULIAN)
        total = motor.n.almacen.ultima_secuencia(alcance, Direccion.remoto_a_local)
        assert total > 3
        primera = await motor.sync_pull(SyncPullEntrada(unidad=alcance, desde=0, limite=2), JULIAN)
        assert [e.secuencia for e in primera.eventos] == [1, 2] and primera.hay_mas
        assert primera.ultima_secuencia == total
        resto = await motor.sync_pull(SyncPullEntrada(unidad=alcance, desde=2, limite=500), JULIAN)
        assert resto.eventos[-1].secuencia == total and not resto.hay_mas
        # El reporte no escribe en local→remoto: esa secuencia es del proxy.
        assert motor.n.almacen.ultima_secuencia(alcance, Direccion.local_a_remoto) == 0

    asyncio.run(caso())


def test_push_idempotente_y_sin_huecos():
    async def caso():
        motor, _ = construir()
        vistos = []
        motor.n.oyentes.append(vistos.append)
        alcance = (await motor.start(entrada_start(), JULIAN)).estado.unidad
        uno, dos = subida(1), subida(2)
        r = await motor.sync_push(SyncPushEntrada(unidad=alcance, eventos=[uno, dos]), JULIAN)
        assert (r.confirmada_hasta, r.duplicados) == (2, 0)
        r = await motor.sync_push(SyncPushEntrada(unidad=alcance, eventos=[dos, subida(3)]), JULIAN)
        assert (r.confirmada_hasta, r.duplicados) == (3, 1)
        with pytest.raises(ErrorNegocio) as exc:
            await motor.sync_push(SyncPushEntrada(unidad=alcance, eventos=[subida(5)]), JULIAN)
        assert exc.value.codigo == CodigoError.secuencia_con_hueco
        with pytest.raises(ErrorNegocio) as exc:
            await motor.sync_push(SyncPushEntrada(unidad=alcance, eventos=[subida(2)]), JULIAN)
        assert exc.value.codigo == CodigoError.secuencia_duplicada
        guardados = motor.n.almacen.eventos_desde(alcance, Direccion.local_a_remoto, 0)
        assert [e.secuencia for e in guardados] == [1, 2, 3]
        assert all(e.actor == JULIAN for e in guardados)
        assert [e.carga.tipo for e in vistos if e.direccion == Direccion.local_a_remoto] == [
            "commit.empujado"
        ] * 3

    asyncio.run(caso())


def test_sync_solo_por_mcp_y_con_alias():
    async def caso():
        motor, _ = construir()
        registro = Registro.del_motor(motor, AutorizadorRoles(motor.n.almacen, abierto=True))
        alcance = (await motor.start(entrada_start(), JULIAN)).estado.unidad
        entrada = {"unidad": alcance.model_dump(mode="json"), "desde": 0}
        r = await registro.invocar("sync_pull", entrada, JULIAN, Superficie.mcp)
        assert r.ok and r.cuerpo["eventos"]
        r = await registro.invocar("sync.pull", entrada, JULIAN, Superficie.http)
        assert r.estado_http == 403
        cuerpo = {"unidad": entrada["unidad"], "eventos": [subida(2).model_dump(mode="json")]}
        r = await registro.invocar("sync_push", cuerpo, JULIAN, Superficie.mcp)
        assert r.estado_http == 409 and r.cuerpo["codigo"] == "secuencia-con-hueco"

    asyncio.run(caso())
