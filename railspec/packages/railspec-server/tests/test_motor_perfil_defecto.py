"""``unit.start`` toma el perfil por defecto del workspace; el pedido explícito gana."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from apoyo_motor import JULIAN, ORG, WS_ALCANCE, construir, entrada_start
from railspec.contracts.comun import AlcanceWorkspace, Perfil
from railspec.contracts.repositorio import Auditoria, Workspace


def correr(coro):
    return asyncio.run(coro)


def _workspace(perfil: Perfil) -> Workspace:
    ahora = datetime(2026, 10, 2, tzinfo=UTC)
    return Workspace(
        version=1,
        auditoria=Auditoria(creado_por=JULIAN, creado_en=ahora, actualizado_por=JULIAN, actualizado_en=ahora),
        alcance=AlcanceWorkspace(org=ORG, workspace=WS_ALCANCE.workspace),
        nombre="Certificados",
        perfil_por_defecto=perfil,
    )


async def _perfil_de_la_unidad(motor, **extra) -> Perfil:
    salida = await motor.start(entrada_start(**extra), JULIAN)
    return salida.estado.perfil


def test_sin_workspace_registrado_nace_estandar():
    async def caso():
        motor, _ = construir()
        assert await _perfil_de_la_unidad(motor) == Perfil.estandar

    correr(caso())


def test_la_unidad_nace_con_el_perfil_por_defecto_del_workspace():
    async def caso():
        motor, _ = construir()
        motor.n.almacen.guardar_configuracion([_workspace(Perfil.profundo)])
        assert await _perfil_de_la_unidad(motor) == Perfil.profundo

    correr(caso())


def test_el_perfil_pedido_gana_al_del_workspace():
    async def caso():
        motor, _ = construir()
        motor.n.almacen.guardar_configuracion([_workspace(Perfil.profundo)])
        assert await _perfil_de_la_unidad(motor, perfil=Perfil.ligero) == Perfil.ligero

    correr(caso())


def test_el_perfil_del_workspace_tambien_se_valida_antes_de_crear_la_unidad():
    async def caso():
        motor, _ = construir()
        motor.n.almacen.guardar_configuracion([_workspace(Perfil.ligero)])
        pedidos = []
        original = motor.n.validar_perfil

        async def espia(ws, nombre, *a, **k):
            pedidos.append(nombre)
            return await original(ws, nombre, *a, **k)

        motor.n.validar_perfil = espia
        await motor.start(entrada_start(), JULIAN)
        assert pedidos == [Perfil.ligero]

    correr(caso())
