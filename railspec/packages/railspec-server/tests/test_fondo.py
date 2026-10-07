"""Tareas periódicas: viven con la aplicación, un fallo no las mata y se detienen al cerrar."""

from __future__ import annotations

import asyncio
import logging
import threading

from railspec.server.api.fondo import TareaFondo
from railspec.server.api.registro import Registro
from railspec.server.api.superficies import aplicacion


async def _hasta(condicion, tope_s: float = 5.0) -> None:
    async with asyncio.timeout(tope_s):
        while not condicion():
            await asyncio.sleep(0.01)


def test_la_tarea_corre_en_un_hilo_aparte_se_repite_y_para_al_cerrar_la_aplicacion():
    corridas: list[int] = []

    def tarea():
        corridas.append(threading.get_ident())

    async def caso():
        app = aplicacion(Registro({}, None), None, fondo=[TareaFondo("prueba", 0.01, tarea)])
        async with app.router.lifespan_context(app):
            await _hasta(lambda: len(corridas) >= 3)
        hechas = len(corridas)
        await asyncio.sleep(0.1)
        assert len(corridas) == hechas  # cerrar la aplicación la detuvo
        assert threading.get_ident() not in corridas  # no bloquea el event loop

    asyncio.run(caso())


def test_una_tarea_que_falla_se_registra_y_se_reintenta(caplog):
    intentos: list[int] = []

    def tarea():
        intentos.append(1)
        if len(intentos) == 1:
            raise RuntimeError("motor caído")

    async def caso():
        app = aplicacion(Registro({}, None), None, fondo=[TareaFondo("rota", 0.01, tarea)])
        with caplog.at_level(logging.ERROR, logger="railspec.server.api.fondo"):
            async with app.router.lifespan_context(app):
                await _hasta(lambda: len(intentos) >= 2)

    asyncio.run(caso())
    assert any("rota" in r.getMessage() for r in caplog.records)


def test_sin_tareas_la_aplicacion_arranca_igual():
    async def caso():
        app = aplicacion(Registro({}, None), None)
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(caso())


def test_el_servidor_con_grafo_retira_las_retenidas_vencidas_al_arrancar_sin_que_llegue_un_indice():
    from datetime import UTC, datetime

    from railspec.contracts.comun import AlcanceRepositorio
    from railspec.contracts.snapshot import DeltaIndice, MotorIndice
    from railspec.graph import AccesoGrafo, AlmacenGrafo, MotorMemoria
    from railspec.server.app import ensamblar
    from railspec.server.config import Configuracion

    DELTA_VACIO = DeltaIndice(motor=MotorIndice(version="0.11.0"))
    alcance = AlcanceRepositorio(org="acme", workspace="certificados", repositorio="api")
    unidad = "0001-firma-pdf"
    motor_grafo = MotorMemoria()
    acceso = AccesoGrafo(motor_grafo)
    # Una unidad integrada hace meses en un commit que ningún índice cubrió.
    antes = AlmacenGrafo(acceso, reloj=lambda: datetime(2020, 1, 1, tzinfo=UTC))
    antes.aplicar_delta(alcance, "a" * 40, DELTA_VACIO, unidad)
    assert antes.retener_superposicion(alcance, unidad, "b" * 40)
    en_curso = "0002-en-curso"
    antes.aplicar_delta(alcance, "a" * 40, DELTA_VACIO, en_curso)
    config = Configuracion.desde_entorno({"RAILSPEC_PERMITIR_DESARROLLO": "1"})

    async def caso():
        _, app = ensamblar(config, motor_grafo=motor_grafo)
        async with app.router.lifespan_context(app):
            await _hasta(lambda: acceso.superposiciones(alcance) == [en_curso])

    asyncio.run(caso())
