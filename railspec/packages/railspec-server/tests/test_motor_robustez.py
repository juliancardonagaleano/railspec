"""Robustez del motor: errores de negocio en vez de excepciones sueltas y efectos sin duplicar."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from apoyo_motor import BASE, JULIAN, REPO, construir, entrada_start
from railspec.contracts.almacen import ConflictoVersion
from railspec.contracts.comun import Modo, Proveedor
from railspec.contracts.estado import TipoCheckpoint
from railspec.contracts.tools import CodigoError, RepositorioInicio, UnitSetModeEntrada
from railspec.server.motor import ErrorNegocio
from railspec.server.motor.gate import SalidaCritico
from railspec.server.proveedores.base import PeticionModelo
from railspec.server.proveedores.cache import CacheNodos, clave_nodo
from test_motor_gate import es_checkpoint, hasta, iniciar


def correr(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("modo", [Modo.supervisado, Modo.desatendido])
def test_set_mode_con_mandato_en_una_unidad_sin_plan_es_error_de_negocio(modo):
    """La entrada trae ``plan`` (su validador lo exige), pero la unidad guardada no pertenece a ninguno."""

    async def caso():
        motor, _ = construir()
        alcance = await iniciar(motor)
        assert alcance.plan is None
        await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.aprobar_spec))
        antes = motor.n.almacen.obtener_estado(alcance)
        entrada = UnitSetModeEntrada(
            unidad=alcance.model_copy(update={"plan": "mandato-x"}),
            modo=modo,
            motivo="pasar a conducción sin checkpoints",
            version_vista=antes.version,
        )
        with pytest.raises(ErrorNegocio) as exc:
            await motor.set_mode(entrada, JULIAN)
        assert exc.value.codigo == CodigoError.conversion_no_permitida
        assert "mandato" in exc.value.detalle
        # Nada se guardó: la unidad sigue legible, en su modo y sin una versión de más.
        despues = motor.n.almacen.obtener_estado(alcance)
        assert despues.modo == antes.modo and despues.version == antes.version

    correr(caso())


def test_conflicto_de_version_no_duplica_auditoria_ni_telemetria():
    """El reintento del bloqueo optimista recalcula el consumo, pero no vuelve a registrar las llamadas."""

    async def caso():
        motor, proveedor = construir()
        alcance = await iniciar(motor)
        almacen = motor.n.almacen
        original = almacen.guardar_estado
        pendientes = {"conflictos": 2}

        def guardar(estado, esperada):
            if (
                pendientes["conflictos"]
                and estado.consumo.tokens > almacen.obtener_estado(estado.unidad).consumo.tokens
            ):
                pendientes["conflictos"] -= 1
                raise ConflictoVersion(esperada, esperada + 1)
            return original(estado, esperada)

        almacen.guardar_estado = guardar
        await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.aprobar_spec))
        assert pendientes["conflictos"] == 0, "la prueba debía provocar los conflictos"
        llamadas = len(proveedor.peticiones)
        assert llamadas > 0
        assert almacen.db.auditoria.count_documents({"evento": "llamada-modelo"}) == llamadas
        assert almacen.db.telemetria.count_documents({}) == llamadas
        estado = almacen.obtener_estado(alcance)
        assert estado.consumo.tokens == llamadas * 1200 and len(estado.modelo_ejecucion) == llamadas

    correr(caso())


def test_la_clave_de_la_cache_distingue_el_commit_del_codigo_evaluado():
    p = PeticionModelo(
        rol="critico-profundo", modelo="claude-opus-5-5", sistema="s", contenido="c", esquema=SalidaCritico
    )
    a = replace(p, commits=("certificados-api@" + "a" * 40,))
    b = replace(p, commits=("certificados-api@" + "b" * 40,))
    claves = {clave_nodo(Proveedor.foundry, x) for x in (p, a, b)}
    assert len(claves) == 3
    assert clave_nodo(Proveedor.foundry, a) == clave_nodo(Proveedor.foundry, replace(a))


def test_un_gate_sobre_otro_commit_no_sale_de_la_cache():
    otro = "9f3c2a1" + "0" * 33

    async def arrancar(motor, commit):
        repos = [RepositorioInicio(repositorio=REPO, rama="main", base_commit=commit)]
        return (await motor.start(entrada_start(repositorios=repos), JULIAN)).estado.unidad

    async def caso():
        motor, proveedor = construir()
        motor.n.proveedores.cache = CacheNodos(motor.n.almacen)
        primera = await arrancar(motor, BASE)
        await hasta(motor, primera, es_checkpoint(TipoCheckpoint.aprobar_spec))
        pagadas = len(proveedor.peticiones)
        # Mismo commit: las mismas entradas salen de la caché y no se pagan.
        misma = await arrancar(motor, BASE)
        await hasta(motor, misma, es_checkpoint(TipoCheckpoint.aprobar_spec))
        assert len(proveedor.peticiones) == pagadas
        # Otro commit: el mismo material es otra pregunta.
        distinta = await arrancar(motor, otro)
        await hasta(motor, distinta, es_checkpoint(TipoCheckpoint.aprobar_spec))
        assert len(proveedor.peticiones) == 2 * pagadas
        assert motor.n.almacen.obtener_estado(distinta).consumo.tokens > 0

    correr(caso())
